# GKE vs. Non-GKE Kubernetes Access

> **Implementation Status:** GKE path — IMPLEMENTED, live-verified. Non-GKE/on-prem path — IMPLEMENTED and live-verified end-to-end through the deployed agent (as of 2026-09-04); one real limitation remains (single-cluster-per-deployment) and Connect Gateway infra registration is still manual, not Terraform-managed.
> **Last Verified:** 2026-09-06 — `agent/mcp_client.py`, `mcp/server.py`, `iac/agent/cloudrun_mcp.tf`, `iac/agent/onprem_fleet.tf`, real investigation runs against `sre-lab`
> **Owner:** SRE Agent platform team.
>
> **Corrected 2026-09-06**: this page previously said "the production agent cannot reach an on-prem cluster today." That was accurate as of 2026-08-08 but is now WRONG — the custom MCP's ingress and connectivity config were fixed 2026-09-04, and multiple real investigations against the on-prem `sre-lab` cluster have succeeded end-to-end through the actual deployed Agent Engine → Agent Gateway → custom MCP → Connect Gateway path since then. See Layer C below for what changed.

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

### Layer C: The deployed Cloud Run MCP service — NOW REACHES the on-prem cluster (fixed 2026-09-04)

This is the layer that actually matters for whether the agent can use this path in production, and it is now working. Three things changed:

1. **Ingress fixed.** `iac/agent/cloudrun_mcp.tf` was `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`, which required an Internal HTTP(S) Load Balancer + Serverless NEG that was never built — the service was unreachable by anyone, including the agent's own authorized traffic (Agent Gateway calls the service's public `.run.app` hostname with a Bearer token, the same pattern GKE Remote MCP's own public endpoint uses). Changed to `INGRESS_TRAFFIC_ALL`. IAM is unaffected and still the real control: only the agent's identity holds `roles/run.invoker` on this specific service (verified live: unauthenticated → 403/404, unauthorized-but-real-identity → 401). See [Security Operations](../governance/security.md#custom-mcp-cloud-run-ingress--security-decision-2026-09-04).
2. **Connectivity configured.** `iac/agent/cloudrun_mcp.tf` now sets `K8S_MCP_KUBE_CONTEXT` (via `var.custom_mcp_kube_context`, wired from the `CUSTOM_MCP_KUBE_CONTEXT` GitHub repo variable). `mcp/Dockerfile` bakes in the Connect Gateway kubeconfig, the `gke-gcloud-auth-plugin`, and the base `google-cloud-cli` package the plugin itself shells out to (a real bug found live: the plugin alone isn't self-sufficient).
3. **IAM granted.** The Cloud Run runtime SA (`sre-k8s-mcp-runtime`) holds `roles/gkehub.gatewayReader` (`iac/agent/onprem_fleet.tf`), needed for the Connect Gateway auth path.

**Live E2E proof, repeated across multiple sessions (most recently 2026-09-06)**: real investigations against `sre-lab` via the actual deployed path — Agent Engine → Agent Gateway (`ALLOWED`, logged) → custom MCP Cloud Run (`200 OK`) → Connect Gateway → the `sre-lab` kind cluster → real pod/event/log data → a correctly-evidenced RCA. `selected_mcp: k8s_mcp` appears in the observability payload for every one of these runs. This is not a standalone/local proof — it goes through the same Agent Engine + Agent Gateway path a real caller would use.

### What is still genuinely open

1. **Connect Gateway fleet registration/RBAC is still manual, not Terraform-managed.** No `google_gke_hub_membership`/`google_gke_hub_feature` resource exists in `iac/` — the fleet membership and `generate-gateway-rbac --apply` step were done by hand and are documented (not automated) in `docs/connect-gateway-onprem.md`. Converting this to Terraform (or at minimum a repeatable wrapper script) is unstarted work.
2. **No schema field distinguishes "reach via Connect Gateway" from "reach via direct GKE endpoint"** in `clusters.json` today — the distinction currently lives in which env var (`K8S_MCP_KUBE_CONTEXT` vs `GKE_CLUSTER_ENDPOINT`/`GKE_CA_CERT_GCS_PATH`) is set on the Cloud Run service, not in the cluster registry itself.
3. **Single-cluster-per-deployment.** `mcp/server.py`'s `get_k8s_clients()` uses `@lru_cache(maxsize=1)` — one Cloud Run revision reaches exactly one non-GKE cluster at a time via its `K8S_MCP_KUBE_CONTEXT`. A second on-prem cluster needs either a second Cloud Run service or a per-request context-selection code change. Out of scope for Phase 1, which only required one non-GKE cluster proven.
4. **`DATA_READ` audit logging is still off** for `connectgateway.googleapis.com` — a full read-only investigation via Connect Gateway leaves no audit trail of what was actually read (only denied/blocked mutation attempts are logged). Turning this on is a project-wide audit-config decision that hasn't been made.
5. **The custom MCP's own tool responses are not protected from malicious content on this path either** — see [Security Operations](../governance/security.md#model-armor--three-distinct-mechanisms-each-with-different-enforcement-corrected-2026-09-06) for the CONTENT_AUTHZ response-side gap and the unmerged application-level guard that addresses it.

See [Adding a Non-GKE / On-Prem Cluster](../runbooks/add-non-gke-cluster.md) for the current runbook, updated to reflect what's actually proven working.

---

**Related pages:** [MCP Architecture](mcp-architecture.md) · [Cluster Routing](cluster-routing.md) · [Adding a Non-GKE / On-Prem Cluster](../runbooks/add-non-gke-cluster.md)
