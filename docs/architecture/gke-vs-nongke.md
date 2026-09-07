# GKE vs. Non-GKE Kubernetes Access

> **Implementation Status:** GKE path — IMPLEMENTED, live-verified. Non-GKE/on-prem path — IMPLEMENTED and live in production, with real end-to-end proof (see Layer C below and [MCP Architecture](mcp-architecture.md)).
> **Last Verified:** 2026-09-07 — `agent/mcp_client.py`, `mcp/server.py`, `docs/connect-gateway-onprem.md`, `docs/custom-k8s-mcp.md`, [MCP Architecture](mcp-architecture.md)
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

### Layer A: Connect Gateway infrastructure itself — proven live, but manual, not Terraform-managed

A local `kind` cluster (`sre-lab`, standing in for a real on-prem site) was registered as a GKE Fleet membership using workload identity federation with a private issuer (`--has-private-issuer` — no static SA key involved; org policy actively blocks static key creation). Read-only RBAC was applied via the official `gcloud container fleet memberships generate-gateway-rbac --apply` helper, granting the built-in **`view`** ClusterRole. This was tested with real `kubectl` calls:

| Call | Result |
|---|---|
| `get pods -A`, `get deployments -A`, `get events -A` | ✅ Succeeded |
| `get nodes` | ❌ Forbidden (`view` excludes cluster-scoped Nodes) |
| `create namespace`, `delete pod`, `get secrets -A` | ❌ Forbidden |

**None of this is Terraform-managed.** No `google_gke_hub_membership`/`google_gke_hub_feature` resource exists anywhere in `iac/`. Every step above was done by hand with `gcloud` and is documented (not automated) in `docs/connect-gateway-onprem.md`. **STATUS: prototype validated, not production infrastructure.**

**Also flagged, still open**: Cloud Audit Logs do not capture *successful* reads through Connect Gateway by default (only denied/blocked mutation attempts are logged) — `DATA_READ` audit logging for `connectgateway.googleapis.com` is off, and turning it on is a project-wide audit-config decision that hasn't been made. **Consequence**: today, a full read-only investigation via Connect Gateway leaves no audit trail of what was actually read.

### Layer B: The custom MCP server's code — has a working Connect Gateway auth branch, proven only locally

`mcp/server.py`'s `get_k8s_clients()` supports three connectivity modes: direct GKE endpoint, Connect Gateway (via a kubeconfig context), or local kubeconfig. The Connect Gateway branch was proven working via a **manual, local** run — 19 tool calls invoked against the `sre-lab` cluster through Connect Gateway (`docs/custom-k8s-mcp.md`), with an integration test (`mcp/tests/test_live_connect_gateway.py`) that exists but is **auto-skipped unless a specific env var is set** — meaning it never runs in CI, only when someone deliberately runs it by hand.

**One call correctly failed, and it's worth knowing the exact shape of that failure**: 18 of the 19 calls returned real data; `list_nodes` returned:
```json
{"ok": false, "error": "... nodes is forbidden ... at the cluster scope ..."}
```
**Why this is a good result, not a bug**: this is the expected consequence of the `view` ClusterRole excluding cluster-scoped Nodes (see [GKE vs Non-GKE Access](#layer-a-connect-gateway-infrastructure-itself--proven-live-but-manual-not-terraform-managed) above) — the important finding is that the server surfaced it as a clean, structured `{"ok": false, "error": ...}` response instead of an unhandled exception crashing the process. That's the `@guarded()` decorator's error-handling working as designed (see [Security Operations](../governance/security.md)), not a gap.

### Layer C: The deployed Cloud Run MCP service — reaches the on-prem cluster in production

This used to be the layer that was missing; it's now closed. The deployed Cloud Run service (`sre-k8s-mcp`) has `K8S_MCP_KUBE_CONTEXT` set (via `var.custom_mcp_kube_context`, `iac/agent/cloudrun_mcp.tf`), and `mcp/Dockerfile` bakes in the Connect Gateway kubeconfig, `gke-gcloud-auth-plugin`, and the base `google-cloud-cli` package the plugin needs. Ingress was changed to `INGRESS_TRAFFIC_ALL` (IAM authorization via `roles/run.invoker` is unaffected and independently verified). See [MCP Architecture](mcp-architecture.md#2-k8s_mcp--custom-cloud-run-mcp-fallback--on-prem-path) for exactly how each of the three original blockers (deploy flag, ingress/networking, connectivity env vars) was resolved.

> **Live E2E proof (2026-09-04, re-confirmed 2026-09-05/06/07)**: a real Agent → Agent Gateway → custom Cloud Run MCP → Connect Gateway → the `sre-lab` on-prem/non-GKE cluster investigation path works, with dozens of successful real investigations recorded. This is no longer a standalone/manual-only result — it is the live production path.

### What's still open

1. Convert the manual `gcloud` Fleet-registration/RBAC steps into Terraform (or at minimum, a repeatable script — there is currently no wrapper script for `generate-gateway-rbac`).
2. Add a field to `clusters.json`'s schema to distinguish "reach via Connect Gateway" from "reach via direct endpoint" — no such field exists today.
3. ~~Fix `clusters.json`'s single-cluster-only template so a second (on-prem) cluster entry survives a `terraform apply`~~ — **DONE 2026-08-09**: `var.additional_clusters` now supports any number of clusters, including non-GKE ones, and every entry survives `terraform apply` by design (Terraform is now the sole source of truth). See [Cluster Routing](cluster-routing.md).
4. Turn on `DATA_READ` audit logging for `connectgateway.googleapis.com`, or accept the current audit gap as a documented risk.
5. The custom MCP is still single-cluster-per-deployment (`get_k8s_clients()`'s `@lru_cache(maxsize=1)`) — a second non-GKE cluster needs either a second Cloud Run service or per-request context selection.

Items 1, 2, and 4 remain manual/undone today — see [Adding a Non-GKE / On-Prem Cluster](../archive/SUPERSEDED_2026-09-07_add-non-gke-cluster.md) (archived, superseded by the live build above) for historical context.

---

**Related pages:** [MCP Architecture](mcp-architecture.md) · [Cluster Routing](cluster-routing.md) · [Adding a Non-GKE / On-Prem Cluster (archived)](../archive/SUPERSEDED_2026-09-07_add-non-gke-cluster.md)
