# GKE vs. Non-GKE Kubernetes Access

> **Implementation Status:** GKE path — IMPLEMENTED, live-verified. Non-GKE/on-prem path — IMPLEMENTED and live in production, with real end-to-end proof (see Layer C below and [MCP Architecture](mcp-architecture.md)). Onboarding a new non-GKE cluster is now an idempotent Ansible workflow (see Layer A below and [the runbook](../runbooks/add-onprem-cluster.md)), live-validated 2026-09-21 including a real Agent Engine investigation.
> **Last Verified:** 2026-09-21 — `agent/mcp_client.py`, `mcp/server.py`, `docs/connect-gateway-onprem.md`, `docs/custom-k8s-mcp.md`, [MCP Architecture](mcp-architecture.md), `ansible/roles/onprem_cluster_onboarding/`, `iac/agent/onprem_fleet.tf`
> **Owner:** SRE Agent platform team.
>
> Three separate layers exist below. All three are now proven — Layer C (the deployed
> Cloud Run service reaching a real cluster in production) is the one that used to be
> the gap; it is now closed, with live evidence.

## The GKE path — IMPLEMENTED

```
Agent → Agent Gateway → GKE Remote MCP (Google-managed) → target GKE cluster
```

This is the live, working, tested path. Cross-project IAM only (`roles/container.viewer`, `roles/mcp.toolUser`, `roles/logging.viewer`, `roles/monitoring.viewer` on the target GKE project) — no Kubernetes RBAC configuration needed, because GKE Remote MCP handles that internally. See [MCP Architecture](mcp-architecture.md#1-gke_remote_mcp--google-managed-gke-remote-mcp) and [Agent Identity](agent-identity.md) for the IAM detail.

## The non-GKE / on-prem path — three layers, evaluate each separately

### Layer A: Connect Gateway infrastructure itself — proven live, now Ansible-automated, still not Terraform-managed (by design)

A local `kind` cluster (`sre-lab`, standing in for a real on-prem site) was registered as a GKE Fleet membership using workload identity federation with a private issuer (`--has-private-issuer` — no static SA key involved; org policy actively blocks static key creation). Read-only RBAC was applied via the official `gcloud container fleet memberships generate-gateway-rbac --apply` helper, granting the built-in **`view`** ClusterRole, plus a narrow supplemental grant for cluster-scoped `nodes: get,list` (see below). This was tested with real `kubectl` calls, both as the operator and, separately, as the impersonated real runtime identity:

| Call | Result |
|---|---|
| `get pods -A`, `get deployments -A`, `get events -A` | ✅ Succeeded |
| `get nodes` | ✅ Succeeded (as of 2026-09-21 — see the parity-fix note below; historically ❌ Forbidden under `view` alone) |
| `create namespace`, `delete pod`, `get secrets -A` | ❌ Forbidden |

**Updated 2026-09-21 — no longer manual.** [`ansible/roles/onprem_cluster_onboarding/`](../../ansible/) automates every step above (registration, RBAC, verification) idempotently, including a mandatory Fleet-tier safety check that did not exist when the real cost incident in commit `cc9dbe0` happened (both prior on-prem memberships landed on `ENTERPRISE` tier, ~$216/month, undetected for weeks). Run it via [Adding a Non-GKE / On-Prem Cluster](../runbooks/add-onprem-cluster.md), not by hand. **This is still deliberately NOT Terraform-managed** — no `google_gke_hub_membership`/`google_gke_hub_feature` resource exists in `iac/`, and `iac/agent/onprem_fleet.tf`'s own header explains why (a prior CI incident where a Terraform local-exec provisioner tore down registration on every apply). Terraform's role is unchanged: one project-level IAM grant (now two — see below) and the cluster's `clusters.json` registry entry. **STATUS: prototype validated in 2026-08, now a repeatable, idempotent, live-validated Ansible workflow (2026-09-21) — not yet validated against a real (non-kind) external cluster over a real network path.**

**Node-read parity gap — closed 2026-09-21.** The `view` ClusterRole excludes cluster-scoped `nodes`, but the MCP tool suite (`mcp/tools/nodes.py`) calls `list_node`/`read_node` as part of normal investigation — a real, live gap for any non-GKE cluster (GKE clusters cover this via Cloud IAM instead). The Ansible role now grants a narrow supplemental `ClusterRole`/`ClusterRoleBinding` for exactly `nodes: get,list` (no `watch` — confirmed unused by any MCP tool call). Also added: `roles/gkehub.viewer` alongside the existing `roles/gkehub.gatewayReader` project IAM grant (`iac/agent/onprem_fleet.tf`) — required only by the `gcloud ... get-credentials` CLI mechanism (used for operator debugging and by Ansible's own verification step), confirmed live that the actual production path (`mcp/server.py`'s direct Connect Gateway REST call) needs only `gatewayReader`.

**Also flagged, still open**: Cloud Audit Logs do not capture *successful* reads through Connect Gateway by default (only denied/blocked mutation attempts are logged) — `DATA_READ` audit logging for `connectgateway.googleapis.com` is off, and turning it on is a project-wide audit-config decision that hasn't been made. **Consequence**: today, a full read-only investigation via Connect Gateway leaves no audit trail of what was actually read.

### Layer B: The custom MCP server's code — has a working Connect Gateway auth branch, proven only locally

`mcp/server.py`'s `get_k8s_clients()` supports three connectivity modes: direct GKE endpoint, Connect Gateway (via a kubeconfig context), or local kubeconfig. The Connect Gateway branch was proven working via a **manual, local** run — 19 tool calls invoked against the `sre-lab` cluster through Connect Gateway (`docs/custom-k8s-mcp.md`), with an integration test (`mcp/tests/test_live_connect_gateway.py`) that exists but is **auto-skipped unless a specific env var is set** — meaning it never runs in CI, only when someone deliberately runs it by hand.

**One call correctly failed at the time of this original test (2026-08-06/07), and it's worth knowing the exact shape of that failure**: 18 of the 19 calls returned real data; `list_nodes` returned:
```json
{"ok": false, "error": "... nodes is forbidden ... at the cluster scope ..."}
```
**Why this was a good result, not a bug, even before the fix**: this was the expected consequence of the `view` ClusterRole excluding cluster-scoped Nodes on that binding — the important finding was that the server surfaced it as a clean, structured `{"ok": false, "error": ...}` response instead of an unhandled exception crashing the process. That's the `@guarded()` decorator's error-handling working as designed (see [Security Operations](../governance/security.md)), not a gap. **Updated 2026-09-21**: this specific `nodes` gap is now closed for any cluster onboarded via the Ansible workflow — see the parity-fix note in Layer A above — so `list_nodes` now succeeds against `sre-lab`, live-confirmed.

### Layer C: The deployed Cloud Run MCP service — reaches the on-prem cluster in production

This used to be the layer that was missing; it's now closed. The deployed Cloud Run service (`sre-k8s-mcp`) has `K8S_MCP_KUBE_CONTEXT` set (via `var.custom_mcp_kube_context`, `iac/agent/cloudrun_mcp.tf`), and `mcp/Dockerfile` bakes in the Connect Gateway kubeconfig, `gke-gcloud-auth-plugin`, and the base `google-cloud-cli` package the plugin needs. Ingress was changed to `INGRESS_TRAFFIC_ALL` (IAM authorization via `roles/run.invoker` is unaffected and independently verified). See [MCP Architecture](mcp-architecture.md#2-k8s_mcp--custom-cloud-run-mcp-fallback--on-prem-path) for exactly how each of the three original blockers (deploy flag, ingress/networking, connectivity env vars) was resolved.

> **Live E2E proof (2026-09-04, re-confirmed 2026-09-05/06/07)**: a real Agent → Agent Gateway → custom Cloud Run MCP → Connect Gateway → the `sre-lab` on-prem/non-GKE cluster investigation path works, with dozens of successful real investigations recorded. This is no longer a standalone/manual-only result — it is the live production path.

### What's still open

1. ~~Convert the manual `gcloud` Fleet-registration/RBAC steps into Terraform (or at minimum, a repeatable script — there is currently no wrapper script for `generate-gateway-rbac`).~~ — **DONE 2026-09-21**, deliberately as an idempotent Ansible workflow rather than Terraform (see Layer A above for why Terraform was ruled out) — `ansible/roles/onprem_cluster_onboarding/`, live-validated including a real Agent Engine investigation. Remaining: not yet validated against a real (non-kind) external cluster over a real network path.
2. ~~Add a field to `clusters.json`'s schema to distinguish "reach via Connect Gateway" from "reach via direct endpoint"~~ — **DONE 2026-09-07**: `fleet_project_number`/`fleet_membership` (dynamic Connect Gateway, no static file) vs. the legacy `kube_context` field (static, image-baked) are now distinct, explicit registry fields — see `docs/connect-gateway-onprem.md`.
3. ~~Fix `clusters.json`'s single-cluster-only template so a second (on-prem) cluster entry survives a `terraform apply`~~ — **DONE 2026-08-09**: `var.additional_clusters` now supports any number of clusters, including non-GKE ones, and every entry survives `terraform apply` by design (Terraform is now the sole source of truth). See [Cluster Routing](cluster-routing.md).
4. Turn on `DATA_READ` audit logging for `connectgateway.googleapis.com`, or accept the current audit gap as a documented risk.
5. ~~The custom MCP is still single-cluster-per-deployment (`get_k8s_clients()`'s `@lru_cache(maxsize=1)`) — a second non-GKE cluster needs either a second Cloud Run service or per-request context selection.~~ — **DONE 2026-09-07** (issue #86): `get_k8s_clients(cluster_id)` is `@lru_cache(maxsize=32)`, keyed per cluster; one shared Cloud Run service serves many clusters with proven isolation.

Item 1 and item 4 remain manual/undone today — see [Adding a Non-GKE / On-Prem Cluster](../archive/SUPERSEDED_2026-09-07_add-non-gke-cluster.md) (archived, superseded by the live build above) for historical context.

---

**Related pages:** [MCP Architecture](mcp-architecture.md) · [Cluster Routing](cluster-routing.md) · [Adding a Non-GKE / On-Prem Cluster (current, Ansible)](../runbooks/add-onprem-cluster.md) · [Connect Gateway on-prem setup (design rationale)](../connect-gateway-onprem.md) · [Adding a Non-GKE / On-Prem Cluster (archived, pre-Ansible manual runbook)](../archive/SUPERSEDED_2026-09-07_add-non-gke-cluster.md)
