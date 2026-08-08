# Runbook: Adding a New GKE Cluster

> **Last Verified:** 2026-08-08 · **Owner:** SRE Agent platform team
> **Prerequisite reading**: [Cluster Routing](../architecture/cluster-routing.md)

## ⚠️ Read this first — the wipe-on-apply limitation

`clusters.json` (the runtime cluster registry) is regenerated from a Terraform template (`iac/agent/clusters.json.tftpl`) that today only supports **one** cluster. Any manually-added second entry survives only until the next `terraform apply`, which will overwrite it. **Fixing this template is a prerequisite for onboarding a second cluster durably** — don't skip step 6 below.

## Steps

1. **Prerequisites**: the target is a real GKE cluster, in a project the agent's cross-project IAM can be extended to reach.
2. **Cluster metadata**: gather `project_id`, `region`, `cluster_name`, `environment` (free text), `allowed_namespaces`.
3. **Agent Identity / IAM authorization**: add the target project to `iac/gke-access/crossproject_iam.tf` (or extend the existing block if it's the same project) — grant exactly `roles/container.viewer`, `roles/mcp.toolUser`, `roles/logging.viewer`, `roles/monitoring.viewer` to the agent's principal(Set). Do not grant anything broader — see [Authorization and Permissions](../governance/security.md).
4. **Gateway/MCP configuration**: no change needed for a GKE cluster — GKE Remote MCP handles routing internally once IAM is granted (see [MCP Architecture](../architecture/mcp-architecture.md)).
5. **Routing configuration**: this is the `clusters.json` entry itself — `name`, `aliases`, `project`, `region`, `type: "gke"`, `environment`, `allowed_namespaces`, `owner`, `enabled: true`.
6. **Terraform changes — fix the multi-cluster gap**: change `iac/agent/clusters.json.tftpl` from a single hardcoded object to a `for` loop over a new `var.clusters` list variable; update `iac/agent/main.tf`'s `local.clusters_json` and `iac/agent/variables.tf` accordingly. (If you don't want to invest in this now, the interim workaround is manually re-uploading the multi-cluster JSON to the bucket after every `terraform apply` — fragile, not recommended for anything beyond a short-term test.)
7. **Deployment**: `terraform apply` in `iac/agent/` and `iac/gke-access/`.
8. **Connectivity test**: `python3 invoke_agent.py --scenario <new-scenario-targeting-this-cluster>` (add a scenario to `invoke_agent.py` if one doesn't exist for this cluster).
9. **Tool test**: confirm at least one `list_*`/`get_*` call succeeds against the new cluster.
10. **Routing test**: confirm `context_resolver` correctly resolves to the new cluster given realistic hints (test all applicable tiers — exact ID, alias, project/env/namespace).
11. **Read-only security verification**: confirm the granted roles are exactly what's expected — no write/delete/exec capability (see [Authorization and Permissions](../governance/security.md)).
12. **RCA test**: run a real (or synthetic) incident scenario end-to-end and review the output RCA for correctness.
13. **Observability verification**: confirm `sre_agent/*` metrics/logs correctly show the new cluster's `run_id`s.
14. **Evaluation test**: add a golden case (`agent/eval/golden_cases.py`) covering this cluster if it represents a genuinely new scenario type.
15. **Production approval**: get sign-off before setting `enabled: true` in the live registry, given the wipe-on-apply risk in step 6 — make sure whoever approves understands that limitation if it wasn't fixed.

---

**Related pages:** [Cluster Routing](../architecture/cluster-routing.md) · [Adding a Non-GKE / On-Prem Cluster](add-non-gke-cluster.md)
