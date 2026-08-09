# Runbook: Adding a New GKE Cluster

> **Last Verified:** 2026-08-09 · **Owner:** SRE Agent platform team
> **Prerequisite reading**: [Cluster Routing](../architecture/cluster-routing.md)

## Multi-cluster support — FIXED (2026-08-09)

`clusters.json` now supports multiple clusters cleanly via `var.additional_clusters` (`iac/agent/variables.tf`, a `map(object(...))`), merged with the always-present default cluster on every apply. Terraform is still the sole source of truth — do not hand-edit the file in GCS, it's overwritten every apply by design. Add a cluster by adding an entry to `var.additional_clusters`, not by editing GCS.

## Steps

1. **Prerequisites**: the target is a real GKE cluster, in a project the agent's cross-project IAM can be extended to reach.
2. **Cluster metadata**: gather `project_id`, `region`, `cluster_name`, `environment` (free text), `allowed_namespaces`.
3. **Check the IAM gap first**: if the target cluster is in the **same** `project_b_id` as the existing cluster, no IAM change is needed — the cross-project grants in `iac/gke-access/crossproject_iam.tf` are project-scoped, not cluster-scoped. If it's in a **different** project, `iac/gke-access` is currently hardwired to one `var.project_b_id` (`iac/gke-access/providers.tf`) — you must apply that stack a second time against the new project (see step 4), or the new cluster will get a `clusters.json` entry but `403` on every tool call. This gap is not fixed by this change; it's reported here so it's not missed.
4. **Agent Identity / IAM authorization** (only if a new project): add/extend `iac/gke-access` for the new project — grant exactly `roles/container.viewer`, `roles/mcp.toolUser`, `roles/logging.viewer`, `roles/monitoring.viewer` to the agent's principal(Set). Do not grant anything broader — see [Authorization and Permissions](../governance/security.md).
5. **Gateway/MCP configuration**: no change needed for a GKE cluster — GKE Remote MCP handles routing internally once IAM is granted (see [MCP Architecture](../architecture/mcp-architecture.md)).
6. **Routing configuration**: add an entry to `var.additional_clusters` in `terraform.tfvars`:
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
   Only `project`/`region` are required — every other field has a sensible default (see `iac/agent/variables.tf`). The key must not collide with `var.gke_cluster_name` — Terraform will refuse to plan if it does (a real `validation` block, not just a warning).
7. **Deployment**: `terraform apply` in `iac/agent/` (and `iac/gke-access/` too, if step 3 found a new-project gap).
8. **Connectivity test**: `python3 invoke_agent.py --scenario <new-scenario-targeting-this-cluster>` (add a scenario to `invoke_agent.py` if one doesn't exist for this cluster).
9. **Tool test**: confirm at least one `list_*`/`get_*` call succeeds against the new cluster.
10. **Routing test**: confirm `context_resolver` correctly resolves to the new cluster given realistic hints (test all applicable tiers — exact ID, alias, project/env/namespace). `tests/test_multi_cluster_registry.py` has a template for this.
11. **Read-only security verification**: confirm the granted roles are exactly what's expected — no write/delete/exec capability (see [Authorization and Permissions](../governance/security.md)).
12. **RCA test**: run a real (or synthetic) incident scenario end-to-end and review the output RCA for correctness.
13. **Observability verification**: confirm `sre_agent/*` metrics/logs correctly show the new cluster's `run_id`s.
14. **Evaluation test**: add a golden case (`agent/eval/golden_cases.py`) covering this cluster if it represents a genuinely new scenario type.
15. **Production approval**: get sign-off before setting `enabled: true` in the live registry.

---

**Related pages:** [Cluster Routing](../architecture/cluster-routing.md) · [Adding a Non-GKE / On-Prem Cluster](add-non-gke-cluster.md)
