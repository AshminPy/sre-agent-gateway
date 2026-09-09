# Runbook: Adding a New GKE Cluster

> **Last Verified:** 2026-09-08 · **Owner:** SRE Agent platform team
> **Prerequisite reading**: [Cluster Routing](../architecture/cluster-routing.md)

## Multi-cluster support — FIXED (2026-08-09)

`clusters.json` now supports multiple clusters cleanly via `var.additional_clusters` (`iac/agent/variables.tf`, a `map(object(...))`), merged with the always-present default cluster on every apply. Terraform is still the sole source of truth — do not hand-edit the file in GCS, it's overwritten every apply by design. Add a cluster by adding an entry to `var.additional_clusters`, not by editing GCS.

## Section 5 correction (2026-09-08) — the Kubernetes RBAC step was missing entirely

This runbook previously never mentioned Kubernetes-native RBAC at all. Following its 15 steps exactly would produce a cluster where `list_pods`/`describe_k8s_resource` work (Cloud IAM's `roles/container.viewer` authenticates to the cluster) but **every log read 403s** — GKE's authorizer accepts *either* a Cloud IAM permission *or* a Kubernetes RBAC grant for the specific resource/verb, and `container.viewer` does not include `pods/log`. This is exactly the failure mode issue #92 hit live. Step 5 below is the fix; it did not exist in any prior version of this document.

**Confirmed from current code:** adding a cluster requires **zero** Python changes (agent or MCP) and **zero** new MCP deployment — GKE Remote MCP is one fixed, Google-managed endpoint; the target cluster is carried as data in each tool call's `parent` field (`agent/mcp_client.py`), never in code. Verified by grep: no cluster name or ID appears anywhere in `agent/mcp_client.py`, `agent/nodes/mcp_router.py`, or `iac/agent/agent_registry*.tf`.

## Steps

1. **Prerequisites**: the target is a real GKE cluster, in a project the agent's cross-project IAM can be extended to reach.
2. **Cluster metadata**: gather `project_id`, `region`, `cluster_name`, `environment` (free text), `allowed_namespaces`.
3. **Check the IAM gap first**: if the target cluster is in the **same** `project_b_id` as the existing cluster, no IAM change is needed — the cross-project grants in `iac/gke-access/crossproject_iam.tf` are project-scoped, not cluster-scoped. If it's in a **different** project, `iac/gke-access` is currently hardwired to one `var.project_b_id` (`iac/gke-access/providers.tf`) — you must apply that stack a second time against the new project (step 7 below), or the new cluster will get a `clusters.json` entry but `403` on every tool call. This gap is not fixed by this change; it's reported here so it's not missed.
4. **Agent Identity / IAM authorization** (only if a new project): add/extend `iac/gke-access` for the new project — grant exactly `roles/container.viewer`, `roles/mcp.toolUser`, `roles/logging.viewer`, `roles/monitoring.viewer` to the agent's principal(Set). Do not grant anything broader — see [Authorization and Permissions](../governance/security.md).
5. **Kubernetes read-only RBAC — REQUIRED, every new cluster, not just new projects**: Cloud IAM's `roles/container.viewer` (step 4) authenticates the agent's identity to the cluster; it does **not** grant `pods/log` reads. Apply a `Role`/`RoleBinding` on the new cluster itself, granting `get`/`list`/`watch` on `pods`, `pods/log`, `events` in the target namespace(s) — see `k8s/rbac.yaml` for the exact, already-live shape (currently applied to `test-incidents` on `sre-test-cluster`). This is a plain `kubectl apply`, not Terraform (Kubernetes RBAC on GKE clusters is managed out-of-band from this repo's Terraform, same operational boundary the on-prem/Connect-Gateway path already uses — see [Connect Gateway on-prem setup](../connect-gateway-onprem.md)).

   **The RoleBinding's `subjects[].name` must reference the live Agent Identity principal**, not a service account key:
   ```
   principal://agents.global.org-<ORG_NUMBER>.system.id.goog/resources/aiplatform/projects/<PROJECT_NUMBER>/locations/<REGION>/reasoningEngines/<REASONING_ENGINE_ID>
   ```
   Get the current values with `terraform -chdir=iac/agent output -raw reasoning_engine_id` (and the project/org numbers from `gcloud projects describe`). **This binding must be re-applied whenever the reasoning engine is recreated** — a new engine gets a new numeric ID, and the binding is scoped to one specific instance, not the whole project. This exact drift caused issue #92's real 403.
6. **Gateway/MCP configuration**: no change needed for a GKE cluster — GKE Remote MCP handles routing internally once IAM + RBAC are granted (see [MCP Architecture](../architecture/mcp-architecture.md)).
7. **Routing configuration**: add an entry to `var.additional_clusters` in `terraform.tfvars`:
   ```hcl
   additional_clusters = {
     "prod-cluster-east" = {
       project     = "your-project-id"
       region      = "us-east1"
       environment = "production"
       aliases     = ["prod-east"]
       owner       = "your-team"
     }
   }
   ```
   Only `project`/`region` are required — every other field has a sensible default (see `iac/agent/variables.tf`). The key must not collide with `var.gke_cluster_name` — Terraform will refuse to plan if it does (enforced via a `lifecycle.precondition` on `google_storage_bucket_object.clusters_json`, `iac/agent/buckets.tf` — moved there from a plain `validation` block on 2026-08-26 because a cross-variable `validation` needs Terraform ≥1.9 and this stack is pinned to 1.4.7 to match company Spacelift policy; the guard is still enforced at plan/apply time either way).
8. **Deployment**: `terraform apply` in `iac/agent/` (and `iac/gke-access/` too, if step 3 found a new-project gap).
9. **Cluster resolution test**: confirm `context_resolver`/`resolve_cluster_routing` correctly resolves to the new cluster given realistic hints (test all applicable tiers — exact ID, alias, project/env/namespace). `tests/test_multi_cluster_registry.py` has a template for this.
10. **Connectivity test**: `python3 invoke_agent.py --scenario <new-scenario-targeting-this-cluster>` (add a scenario to `invoke_agent.py` if one doesn't exist for this cluster).
11. **Tool test**: confirm at least one `list_*`/`get_*` call succeeds against the new cluster, **and specifically confirm a log read (`get_current_logs`/`get_previous_logs`) succeeds** — this is the exact call step 5's RBAC grant protects; a missing RBAC grant will not show up in a `list_pods`-only test.
12. **Read-only security verification**: confirm the granted roles/RBAC are exactly what's expected — no write/delete/exec capability (see [Authorization and Permissions](../governance/security.md)).
13. **RCA test**: run a real (or synthetic) incident scenario end-to-end and review the output RCA for correctness.
14. **Observability verification**: confirm `sre_agent/*` metrics/logs correctly show the new cluster's `run_id`s.
15. **Evaluation test**: add a golden case (`agent/eval/golden_cases.py`) covering this cluster if it represents a genuinely new scenario type.
16. **Production approval**: get sign-off before setting `enabled: true` in the live registry.

## What this runbook does NOT cover

Live-testing this exact process against a genuinely new, second GKE cluster (distinct from `sre-test-cluster`) — confirmed via git history and live GCS/GKE queries (2026-09-08) that no second GKE cluster has ever actually been onboarded this way. This document describes the correct, config-only mechanism (verified from current code); it is not yet proof that a second cluster onboarding has been exercised end-to-end. That live proof is intentionally deferred to a follow-up session with access to provision a second real GKE cluster.

---

**Related pages:** [Cluster Routing](../architecture/cluster-routing.md) · [Connect Gateway (on-prem cluster setup, including "Adding a second on-prem cluster")](../connect-gateway-onprem.md)
