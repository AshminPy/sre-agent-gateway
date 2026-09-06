# MCP Architecture

> **Implementation Status:** IMPLEMENTED — both GKE Remote MCP and the custom MCP (non-GKE/on-prem path) are live-verified. See the 2026-09-04 update below for the custom MCP.
> **Last Verified:** 2026-09-04 — `agent/mcp_client.py`, `mcp/server.py`, `iac/agent/cloudrun_mcp.tf`, `iac/agent/onprem_fleet.tf`
> **Source of Truth:** `agent/mcp_client.py:98-113` (the MCP registry)
> **Owner:** SRE Agent platform team.

## What MCP is

MCP (Model Context Protocol) is an open standard for how an AI agent calls external tools. Instead of writing custom integration code for every system a model might need to query, you stand up an **MCP server** that exposes a fixed set of **tools** (each with a name, description, and a JSON schema for its inputs/outputs). An **MCP client** — our agent — sends a structured "call this tool with these arguments" request and gets back a structured response. It's conceptually similar to a REST API, but with a schema convention specifically designed for LLMs to reason about.

## Why we use MCP instead of writing every integration directly

Two reasons that matter operationally: (1) Google offers a **managed** MCP server for GKE (see below) that we don't have to build or operate ourselves, and (2) it gives us a clean seam for adding future sources (Elastic, Prometheus, etc.) — see [Adding a New MCP Server](../runbooks/add-mcp-server.md) — without rewriting the agent's core reasoning code.

## Our architecture

```
Agent (agent/mcp_client.py) → Agent Gateway → MCP source → target system
```

Two MCP sources are registered in code (`MCP_REGISTRY`, `agent/mcp_client.py:98-113`):

### 1. `gke_remote_mcp` — Google-managed GKE Remote MCP

- **Purpose**: primary source for GKE cluster investigation.
- **Location**: fixed Google endpoint, `https://container.googleapis.com/mcp/read-only`.
- **Deployment model**: fully Google-managed — we don't deploy or operate any server for this.
- **Tools exposed**: 6 (see [Tool Selection](tool-selection.md)).
- **Authentication**: GCP access token via Workload Identity/ADC.
- **Authorization**: Agent Identity's cross-project IAM roles on the target GKE project (`roles/container.viewer`, `roles/mcp.toolUser`, `roles/logging.viewer`, `roles/monitoring.viewer` — see [Authorization and Permissions](../governance/security.md)).
- **Network path**: through Agent Gateway, over Google's backbone.
- **Timeout / failure behavior**: on any non-200 response, the agent auto-falls back to the custom MCP (`_map_to_custom_tool()`, `agent/mcp_client.py:324-334,443-457`).
- **Logging**: failures logged to `sre-agent-tool-failures` with `mcp_source="gke_remote_mcp"`.
- **Owner**: Google (the service itself); SRE Agent platform team (the IAM/routing wiring).
- **Maturity flag**: Google labels this **Preview / Pre-GA** (`agent/mcp_client.py:105`) — a vendor-maturity caveat to be aware of, not a defect in our code.
- **STATUS: IMPLEMENTED, live-verified** as the primary/default path — see `../../archive/RESOLVED_2026-07-17_CURRENT_STATE.md`.

### 2. `k8s_mcp` — custom Cloud Run MCP (fallback / on-prem path)

- **Purpose**: intended fallback when GKE Remote MCP fails, and the intended path for non-GKE/on-prem clusters (which GKE Remote MCP cannot reach at all).
- **Location**: `iac/agent/cloudrun_mcp.tf` — a Cloud Run service, `sre-k8s-mcp`.
- **Deployment model**: our own container (`mcp/server.py`, FastMCP-based), 27 read-only tools, wrapped by a `@guarded()` security decorator (rate limiting, input validation, response redaction, audit logging — see [Authorization and Permissions](../governance/security.md)).
- **Authentication**: Cloud Run identity token.
- **Authorization**: `roles/run.invoker` on the specific service.
- **Network path**: `ingress = INGRESS_TRAFFIC_ALL` (`iac/agent/cloudrun_mcp.tf`, changed from `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` 2026-09-04 — see the STATUS note below and [Security Operations](../governance/security.md#custom-mcp-cloud-run-ingress--security-decision-2026-09-04) for why `ALL` is safe here: `roles/run.invoker` is the actual control, not network isolation).
- **Owner**: SRE Agent platform team.

**STATUS (2026-09-04): OPERATIONAL — the 3 blockers below are resolved, each with live evidence.** The original PLANNED/BLOCKED finding (kept below for history) was accurate at the time it was written:

1. ~~`enable_custom_mcp` defaults to `false`~~ — resolved: the GitHub repo variable `ENABLE_CUSTOM_MCP=true` (confirmed via `gh variable list`) has driven every CI apply since 2026-08-07; the service is live (`sre-k8s-mcp`, revision serving 100% traffic).
2. ~~`INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` requires an Internal Load Balancer + Serverless NEG that doesn't exist~~ — resolved differently than originally proposed: rather than building the LB/NEG (which Agent Gateway's actual call pattern — the public `.run.app` hostname + Bearer identity token — was never going to route through anyway), `iac/agent/cloudrun_mcp.tf`'s ingress was changed to `INGRESS_TRAFFIC_ALL`. IAM authorization (`roles/run.invoker`, scoped to the agent's identity only) is unaffected — verified via Cloud Run's own docs that ingress and IAM are enforced independently, and via a live unauthenticated-request test (403/404, no content) and a live unauthorized-identity test (401) after the change.
3. ~~Connectivity env vars (`K8S_MCP_KUBE_CONTEXT`) unset~~ — resolved: `iac/agent/cloudrun_mcp.tf` now sets `K8S_MCP_KUBE_CONTEXT` (via `var.custom_mcp_kube_context`), and `mcp/Dockerfile` bakes in the Connect Gateway kubeconfig + `gke-gcloud-auth-plugin` + the base `google-cloud-cli` package the plugin actually shells out to (a real bug found live: the plugin alone isn't self-sufficient — `docs/connect-gateway-onprem.md` and this file's own prior research didn't know this until the container's own error log showed it).

**Live E2E proof (2026-09-04, run `run_20260904_215412_kiny`)**: `invoke_agent.py --scenario onprem` against the real deployed agent → Agent Gateway (`ALLOWED`, logged) → custom MCP Cloud Run (`200 OK`) → Connect Gateway → the `sre-lab` kind cluster → real pod/event data → RCA correctly identified the fixture's real broken image (`gcr.io/google-containers/nonexistent-image:v99.9.9`), confidence 1.0, zero fabrication, zero tool failures.

**Known limitation, not fixed by this work**: the custom MCP is still single-cluster-per-deployment (`get_k8s_clients()`'s `@lru_cache(maxsize=1)`) — one Cloud Run revision can point at exactly one non-GKE cluster at a time via its `K8S_MCP_KUBE_CONTEXT` env var. Adding a second non-GKE cluster needs either a second Cloud Run service or a per-request context-selection code change (tracked alongside issue #86's cross-project GKE gap) — out of scope for Phase 1, which only requires one non-GKE cluster proven.

## Agent Registry — how MCP servers/tools get registered

| Pattern | Used for | Mechanism | Example |
|---|---|---|---|
| `--endpoint-spec-type=no-spec` | Plain Google API passthrough hostnames (not MCP tool servers) | `scripts/register_endpoints.py` (imperative script, run in CI) | `*-aiplatform.googleapis.com` |
| `--mcp-server-spec-type=tool-spec` | Real MCP tool servers, including our custom one | **Terraform-managed** since 2026-09-04: `google_agent_registry_service.custom_mcp` (`iac/agent/agent_registry_mcp.tf`) | The tool spec for `sre-k8s-mcp` |

**Corrected 2026-09-06**: `scripts/register_custom_mcp.py` is retired (`archive/RETIRED_2026-09-04_register_custom_mcp.py`) — the custom MCP's registration is no longer a separate imperative script step. `mcp_server_spec.content` is generated by `scripts/build_mcp_tool_spec.py` into `mcp/tool_spec.json`, which Terraform reads via `file()`. CI enforces a health-gated ordering (pin the spec to what's currently live → apply the new Cloud Run revision → verify it's healthy → regenerate the spec to match the new code → apply again) so the registry and the running server never disagree about what's supported — see `iac/agent/agent_registry_mcp.tf`'s own header comment for the full reasoning.

The custom MCP is enabled live (§2 above) — this registration path runs for real on every CI apply that touches `mcp/**`.

## Model Armor coverage on this path (2026-09-06)

Both MCP sources go through the same Agent Gateway CONTENT_AUTHZ path — genuine REQUEST-side inspection/blocking, but RESPONSE-side inspection never fires for either source (a confirmed Google platform limitation, not something fixable in this repo's Terraform). A new, unmerged application-level guard (`mcp/response_guard.py`, branch `poc/mcp-response-guard-model-armor`) closes this specifically for the custom MCP by calling Model Armor directly from the FastMCP server itself. Full detail, evidence, and the still-open fail-open/fail-closed decision: [Security Operations](../governance/security.md#model-armor--three-distinct-mechanisms-each-with-different-enforcement-corrected-2026-09-06).

---

**Related pages:** [Agent Gateway](agent-gateway.md) · [Dynamic MCP Routing](dynamic-mcp-routing.md) · [GKE vs Non-GKE Kubernetes Access](gke-vs-nongke.md) · [Adding a New MCP Server](../runbooks/add-mcp-server.md) · [Security Operations](../governance/security.md)
