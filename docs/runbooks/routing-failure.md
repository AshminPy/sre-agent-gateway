# Runbook: Cluster and Investigation Routing Failures

> **Last Verified:** 2026-08-08 · **Owner:** SRE Agent platform team

## 13. Wrong cluster selected

**Symptom**: the RCA is about a different cluster than the actual incident.

**Likely causes**: a hint in the incoming payload was wrong; an alias in `clusters.json` maps ambiguously.

**How to verify**: check `cluster_routing_method`/`cluster_routing_reason` in the `sre-agent-investigations` log entry for that `run_id` — this tells you exactly which of the 5 routing tiers resolved it and why (see [Cluster Routing](../architecture/cluster-routing.md)).

**Resolution**: fix the source of the bad hint (upstream alert metadata, or a misconfigured alias in `clusters.json`). The routing logic itself never *guesses* — if this happened, a real, verified-looking hint pointed at the wrong place.

## 14. Cluster unreachable

**Symptom**: cluster identity resolves fine, but every tool call against it fails.

**How to verify**: `sre-agent-tool-failures` for that cluster — check the actual error (network, auth, or the cluster genuinely being down/deleted).

**Resolution**: this is a tool-call-level issue, not a routing issue — see [MCP Failure runbook](mcp-failure.md) and [Identity Failure runbook](identity-failure.md) depending on the specific error.

## Investigation-level: unresolved cluster (safe-stop)

**Symptom**: `SRE Agent — Unknown/Ambiguous Cluster Safe-Stop` fires; the RCA explains it couldn't determine a cluster.

**Verify**: `jsonPayload.cluster_routing_method="unresolved"` in the logs for that run — check what hints *were* present in the original payload.

**Resolution**: this is the system working as designed (refusing to guess) — the fix is improving the upstream alert/payload quality, or adding a missing alias/project-env-namespace mapping to `clusters.json` so a legitimate hint resolves next time.

---

**Related pages:** [Cluster Routing](../architecture/cluster-routing.md) · [Dynamic MCP Routing](../architecture/dynamic-mcp-routing.md) · [MCP Failure](mcp-failure.md)
