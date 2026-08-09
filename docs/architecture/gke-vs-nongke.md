# GKE vs. Non-GKE Kubernetes Access

> **Implementation Status:** GKE path — IMPLEMENTED, live-verified. Non-GKE/on-prem path — PARTIALLY IMPLEMENTED, proven only standalone, NOT wired into the deployed agent.
> **Last Verified:** 2026-08-08 — `agent/mcp_client.py`, `mcp/server.py`, `docs/connect-gateway-onprem.md`, `docs/custom-k8s-mcp.md`
> **Owner:** SRE Agent platform team.
>
> **Read this page carefully before telling anyone "on-prem support is done."** It is not. Three separate layers exist, and only the first is actually proven in a way that matters for production.

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

### Layer C: The deployed Cloud Run MCP service — does NOT reach any cluster

This is the layer that actually matters for whether the agent can use this path in production, and it's the one that's missing. The deployed Cloud Run service's only env var is `PROJECT_ID` — neither the direct-GKE connectivity vars nor the Connect Gateway `K8S_MCP_KUBE_CONTEXT` var is set anywhere in Terraform. Left unset, the code falls through to a "load local kubeconfig" branch with no kubeconfig file available inside a Cloud Run container.

**Combined with [MCP Architecture](mcp-architecture.md)'s finding that the custom MCP isn't even deployed (`enable_custom_mcp=false`) and has no network path (no Load Balancer/NEG) even if it were** — the honest summary is:

> **Connect Gateway works when tested manually with `kubectl` or a local `pytest` run. The production agent cannot reach an on-prem/non-GKE cluster today.**

### What's needed to actually wire this together

1. Deploy the custom MCP (`enable_custom_mcp=true`) — but first build the missing Internal Load Balancer + Serverless NEG (see [MCP Architecture](mcp-architecture.md)).
2. Set `K8S_MCP_KUBE_CONTEXT` (or equivalent) on the Cloud Run service, and bake `gke-gcloud-auth-plugin`/`gcloud` into `mcp/Dockerfile`.
3. Grant the Cloud Run runtime SA `roles/gkehub.gatewayReader` (it currently only has `roles/container.viewer`).
4. Convert the manual `gcloud` Fleet-registration/RBAC steps into Terraform (or at minimum, a repeatable script — there is currently no wrapper script for `generate-gateway-rbac`).
5. Add a field to `clusters.json`'s schema to distinguish "reach via Connect Gateway" from "reach via direct endpoint" — no such field exists today.
6. ~~Fix `clusters.json`'s single-cluster-only template so a second (on-prem) cluster entry survives a `terraform apply`~~ — **DONE 2026-08-09**: `var.additional_clusters` now supports any number of clusters, including non-GKE ones, and every entry survives `terraform apply` by design (Terraform is now the sole source of truth). See [Cluster Routing](cluster-routing.md). What's still open for on-prem specifically is items 1-5 above (Connect Gateway networking/auth/RBAC), not the registry-survival issue.
7. Turn on `DATA_READ` audit logging for `connectgateway.googleapis.com`, or accept the current audit gap as a documented risk.

None of this is started as Terraform/automation today — see [Adding a Non-GKE / On-Prem Cluster](../runbooks/add-non-gke-cluster.md) for the current manual runbook based on what's actually been proven.

---

**Related pages:** [MCP Architecture](mcp-architecture.md) · [Cluster Routing](cluster-routing.md) · [Adding a Non-GKE / On-Prem Cluster](../runbooks/add-non-gke-cluster.md)
