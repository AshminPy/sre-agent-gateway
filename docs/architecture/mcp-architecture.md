# MCP Architecture

> **Implementation Status:** PARTIALLY IMPLEMENTED — GKE Remote MCP path is IMPLEMENTED and live-verified; custom MCP is PLANNED/BLOCKED (see below)
> **Last Verified:** 2026-08-08 — `agent/mcp_client.py`, `mcp/server.py`, `iac/agent/cloudrun_mcp.tf`
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
- **Network path**: `ingress = INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` (`iac/agent/cloudrun_mcp.tf:40`).
- **Owner**: SRE Agent platform team.

**STATUS: PLANNED / BLOCKED — not operational today.** This is important, so stated plainly, with the exact evidence:

1. `enable_custom_mcp` defaults to `false` and is **not overridden** in the live `iac/agent/terraform.tfvars` — the Cloud Run service isn't even deployed in the live project today.
2. Even if deployed, `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` requires an Internal Load Balancer + Serverless NEG pointed at the service for **anything** to reach it — including the agent's own traffic. **No such Load Balancer or NEG exists anywhere in this repo's Terraform** — confirmed by an exhaustive search of `iac/` for `serverless_neg`, `forwarding_rule`, `backend_service`, `url_map`, `target_https_proxy`.
3. Even the deployed container's own env vars (`iac/agent/cloudrun_mcp.tf:50-58`) set only `PROJECT_ID` — none of the connectivity variables `mcp/server.py`'s code needs to actually reach a cluster (`GKE_CLUSTER_ENDPOINT`, `K8S_MCP_KUBE_CONTEXT`) are set. Left unset, the server's `get_k8s_clients()` falls through to a "load local kubeconfig" branch that has no kubeconfig file to load inside a Cloud Run container.

**Bottom line**: the custom MCP server's *code* is complete and has been proven to work when run and tested manually/locally against a real cluster via Connect Gateway (see [GKE vs Non-GKE Access](gke-vs-nongke.md)), but the *deployed* Cloud Run service, as currently configured, cannot reach any cluster. Treat any claim that "the custom MCP is a working fallback" as inaccurate until the Load Balancer/NEG and connectivity env vars are actually built.

## Agent Registry — how MCP servers/tools get registered

Two distinct registration patterns exist, used for different things:

| Pattern | Used for | Script | Example |
|---|---|---|---|
| `--endpoint-spec-type=no-spec` | Plain Google API passthrough hostnames (not MCP tool servers) | `scripts/register_endpoints.py` | `*-aiplatform.googleapis.com` |
| `--mcp-server-spec-type=tool-spec` | Real MCP tool servers, including our custom one | `scripts/register_custom_mcp.py` | The 27-tool spec for `sre-k8s-mcp` |

The `tool-spec` pattern has a hard **10KB content limit** enforced by the registration script before it even attempts the `gcloud` call (`scripts/register_custom_mcp.py:78-86`) — this is why the registered spec includes only `name`/`description`/`inputSchema` per tool, not the fuller `outputSchema`/`meta`.

Given the custom MCP is currently disabled (§2 above), this registration path is presently a no-op in the live deployment.

---

**Related pages:** [Agent Gateway](agent-gateway.md) · [Dynamic MCP Routing](dynamic-mcp-routing.md) · [GKE vs Non-GKE Kubernetes Access](gke-vs-nongke.md) · [Adding a New MCP Server](../runbooks/add-mcp-server.md)
