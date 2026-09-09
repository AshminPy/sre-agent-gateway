# ADR-004: MCP as the tool-access protocol

Status: Accepted (GKE Remote MCP path); custom MCP path also live and operational in production — see Tradeoffs

## Context

The agent needs to call live Kubernetes/GKE tooling to investigate incidents,
and eventually other observability sources (Elastic, Prometheus, etc. — see
[Adding a New MCP Server](runbooks/add-mcp-server.md)). Two integration
strategies were available: write direct, source-specific integration code
inside the agent for each system it needs to query, or standardize on a
protocol that separates "what tools exist and how to call them" from "how the
agent's reasoning code works."

## Decision

Use MCP (Model Context Protocol) as the tool-access layer for every external
source the agent calls. Two MCP sources are registered
(`MCP_REGISTRY`, `agent/mcp_client.py:98-113`):

- **`gke_remote_mcp`** — Google's own managed MCP server for GKE
  (`https://container.googleapis.com/mcp/read-only`), exposing 6 tools. We
  don't build or operate this server at all.
- **`k8s_mcp`** — our own Cloud Run-hosted MCP server (`mcp/server.py`,
  FastMCP-based), exposing 27 tools, for non-GKE/on-prem clusters and as an
  intended GKE fallback (see [ADR-012](ADR-012-gke-remote-mcp-vs-custom-mcp.md)
  for that split's own status).

The agent (`agent/mcp_client.py`) is the MCP client throughout; `mcp_router`
picks one source, then one tool + arguments from that source's fixed
allowlist per graph step.

## Alternatives Considered

- **Direct, source-specific integration code** (e.g. calling the GKE API or a
  Kubernetes client library straight from agent nodes, one bespoke code path
  per system) — rejected because it would couple the agent's core reasoning
  code to every external system's own API shape, and rules out reusing
  Google's managed GKE Remote MCP server, which we'd otherwise have to
  reimplement and operate ourselves.
- **A single custom-built MCP server for everything** (skip Google's managed
  GKE Remote MCP, always use our own Cloud Run service) — rejected because it
  would mean operating and securing a GKE-reaching server ourselves for the
  common case (GKE clusters), when Google already offers this as a managed
  service with no server for us to run or patch.

## Reason

MCP gives a schema-defined contract (tool name, description, JSON input/output
schema) between the agent's reasoning code and whatever system a tool call
actually reaches. That contract is what makes two very different backends —
a Google-managed endpoint and our own Cloud Run container — look identical
from `mcp_router`'s point of view: pick a source, pick a tool from its
allowlist, call it. It is also the seam that lets a future source (Elastic,
Prometheus) be added without touching the agent's planning/evaluation logic —
see [MCP Architecture](architecture/mcp-architecture.md).

## Tradeoffs

- No integration code to write or maintain for GKE specifically — Google
  operates `gke_remote_mcp` entirely; but that source is labeled
  **Preview/Pre-GA** by Google (`agent/mcp_client.py:105`), a vendor-maturity
  caveat outside our control.
- The custom `k8s_mcp` server's code is real and complete (11 tool modules,
  27 tools, a live-passing `mcp/tests/test_no_mutation.py` regression test)
  and is **operational in production**: the service is live (`sre-k8s-mcp`),
  and a real end-to-end path (Agent → Agent Gateway → custom Cloud Run MCP →
  Connect Gateway → the `sre-lab` on-prem/non-GKE cluster) has run dozens of
  successful real investigations — see
  [MCP Architecture](architecture/mcp-architecture.md) for the live evidence
  and the small remaining known limitation (single-cluster-per-deployment).
- Tool discovery is static, not dynamic: the agent uses compile-time
  allowlists (`GKE_REMOTE_TOOLS`, `CUSTOM_K8S_TOOLS`), not a runtime
  `tools/list` call — confirmed absent by direct code search. This is a
  deliberate simplicity/predictability tradeoff, not an MCP protocol
  limitation.
- On any non-200 response from `gke_remote_mcp`, the agent auto-falls back to
  the custom MCP (`_map_to_custom_tool()`, `agent/mcp_client.py:324-334,443-457`)
  — since the custom MCP is now reachable in production, this fallback path
  is a working handoff, not a degraded failure.

## Related ADRs

- [ADR-003: LangGraph as the orchestration framework](ADR-003-langgraph-orchestration.md)
- [ADR-005: Read-only by design](ADR-005-read-only-by-design.md)
- [ADR-012: GKE Remote MCP vs custom MCP](ADR-012-gke-remote-mcp-vs-custom-mcp.md)
