# Scaling

> **Implementation Status:** Reference/analysis page based on current implementation limits.
> **Last Verified:** 2026-08-08
> **Owner:** SRE Agent platform team.

## More incidents: 10/day → 100 → 1,000

**10-100/day**: no changes needed — `min_instances=2` provides headroom, and each investigation is independent (no shared mutable state across concurrent runs at the graph level, see [Context and State](../architecture/context-and-state.md)).

**100-1,000/day**: `max_instances` is currently **unset** in Terraform (platform default applies, unverified in this pass — see [Agent Engine](../architecture/agent-engine.md)) — confirm this ceiling before assuming the platform will scale to meet this volume unattended. Cost scales roughly linearly with volume (see [Cost Management](cost-management.md)) — worth a budget conversation before this jump, not after. Cloud Logging/Trace ingestion volume also scales linearly; the double-emission issue (see [Observability](../operations/observability.md)) means this is a good point to fix that before volume makes it more expensive.

## More clusters

The single-cluster-only `clusters.json` template is the actual blocker today, not a scaling limit per se — see [Cluster Routing](../architecture/cluster-routing.md#known-operational-limitation). Fix that first; once fixed, the routing chain itself (deterministic, registry-lookup-based) has no inherent scale limit tied to cluster count. The 5-minute TTL cache on the registry means adding a cluster takes up to 5 minutes to propagate without a container restart.

## More MCP servers

Each new source is an addition to `agent/mcp_client.py`'s static registry and a Phase-1 routing branch in `mcp_router.py` — no architectural limit, but each one is currently a manual code change (see [Adding a New MCP Server](../runbooks/add-mcp-server.md)), not a config-only addition. At some point (many sources), consider whether the static-allowlist pattern should become data-driven/config-driven rather than requiring a code change per source.

## More tools — when does tool-selection complexity become a concern?

Today: 6 tools (GKE Remote MCP) + 27 tools (custom MCP, per-source, never combined). The Phase-2 router prompt only ever shows the model tools from **one already-selected source** — this is a deliberate design choice that keeps the per-call tool list bounded regardless of total tool count across all sources. If a single source's tool count grew very large (100s), the description-based selection mechanism (short strings, no rich schema shown to the model) would likely need revisiting — no evidence in this repo of that threshold being tested.

## More data — preventing context explosion

Already addressed by design: raw tool output never enters `AgentState`; only compressed evidence digests do; `max_steps=5` bounds total loop iterations regardless of how much data any single incident type could theoretically generate. See [Context and State](../architecture/context-and-state.md#how-is-context-size-controlled).

## More teams — ownership and IAM boundary changes

Currently one owning team, one GCP project pair. If multiple teams need to operate independent agent deployments (e.g., per business unit), the natural boundary is a separate `iac/agent/` stack per team (own project, own Agent Identity, own gateway) rather than sharing one deployment — this keeps blast radius and IAM boundaries clean, consistent with how Agent Identity is scoped per-engine already.

## Single orchestrator vs. orchestrator + specialized agents

**Not recommended today, and this document deliberately does not recommend it** — the current single-graph design handles the actual demonstrated workload (Kubernetes RCA investigations) well within its limits (5 iterations, 9-minute timeout, bounded token budget). A multi-agent architecture (e.g., a Kubernetes-specialist agent + a logging-specialist agent + an orchestrator routing between them) would only be justified by a **measurable** scaling requirement this design can't meet — e.g., genuinely needing parallel multi-domain investigation within a single incident, or tool-selection complexity that's actually been observed to degrade accuracy at a specific tool-count threshold. Neither has been demonstrated yet. Revisit this specific question if/when: (a) investigation types diversify well beyond Kubernetes, (b) a single incident genuinely needs simultaneous investigation across unrelated domains (not just sequential tool calls), or (c) tool-selection accuracy measurably degrades as more sources are added.

---

**Related pages:** [Cost Management](cost-management.md) · [Capacity and Quotas](capacity.md) · [Reliability and Failure Modes](reliability.md)
