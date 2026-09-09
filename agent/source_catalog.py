"""agent/source_catalog.py — Section 6: capability-based MCP/data-source selection.

A validated, explicit catalog of non-Kubernetes evidence sources the agent MAY use,
in addition to the existing GKE Remote MCP / custom K8s MCP path. Adding a source
here does NOT, by itself, change routing behavior for any investigation unless the
entry is `enabled` AND authorized for the resolved cluster AND a real evidence gap
matches one of its declared `capabilities`. With every entry disabled (today's real
state), agent/nodes/mcp_router.py's routing is unchanged from before this module
existed — see tests/test_source_catalog.py's
test_no_enabled_source_preserves_kubernetes_only_behavior.

Deliberately a SMALL, EXPLICIT, hand-maintained dict — not a plugin registry, not
runtime discovery of arbitrary sources. See docs/runbooks/add-mcp-server.md for the
full registration checklist (REQUEST_AUTHZ, CONTENT_AUTHZ, IAM, tracing, rollback,
etc.) a new entry here is expected to also satisfy before being enabled.
"""
from typing import Any, Dict, Optional

# ── Capability vocabulary ────────────────────────────────────────────
# Fixed, small set. capability_needed() below only ever matches against these —
# extending this list is a deliberate code change, not something a prompt or a
# runtime value can add to.
CAPABILITY_KUBERNETES_STATE   = "kubernetes_state"    # already served by GKE Remote MCP / k8s_mcp
CAPABILITY_KUBERNETES_EVENTS  = "kubernetes_events"   # already served by GKE Remote MCP / k8s_mcp
CAPABILITY_APPLICATION_LOGS   = "application_logs"    # already served by GKE Remote MCP / k8s_mcp
CAPABILITY_HISTORICAL_METRICS = "historical_metrics"  # NOT served by either K8s source
CAPABILITY_DEPLOYMENT_CHANGES = "deployment_changes"  # partially served (ReplicaSet history) — an
                                                       # external source could do more (e.g. Grafana
                                                       # annotations), not implemented here

# ── Catalog ───────────────────────────────────────────────────────────
SOURCE_CATALOG: Dict[str, Dict[str, Any]] = {
    "prometheus": {
        "source_type":  "prometheus",
        # Real endpoint is read from PROMETHEUS_URL at call time (agent/sources/
        # prometheus_adapter.py) — never hardcoded here. Empty until a real
        # instance is configured.
        "endpoint_env": "PROMETHEUS_URL",
        "auth_ref":     "PROMETHEUS_URL",  # no secret lives in this catalog; documents
                                            # where the real connection info comes from
        # NOT VERIFIED (2026-09-07): no live Prometheus instance exists in this
        # environment to validate against. Built and unit-tested against
        # Prometheus's own documented HTTP API shape with fixtures, per this
        # section's explicit "mark NOT VERIFIED, never completed" allowance.
        # Do not flip to True without a real endpoint AND a real live-validation
        # run through this exact adapter.
        "enabled": False,
        "allowed_clusters": [],  # empty = not cluster-restricted once enabled
        "capabilities": frozenset({CAPABILITY_HISTORICAL_METRICS}),
        "approved_tools": frozenset({"query_range"}),
        "output_adapter": "agent.sources.prometheus_adapter",
        # ── LLM-driven single-query routing (mcp_router.py's Section 6 block) ──
        # Adding a future source (Elastic, Grafana, git MCP) needs ONLY a new
        # catalog entry + a new adapter module exposing a function named
        # `primary_tool` with signature (cluster_id, <query_field>, start_ts,
        # end_ts) -- mcp_router.py and mcp_client.py read these three fields
        # generically and never hardcode a source name or query language.
        #
        # Correction (2026-09-08 audit): this comment used to cite
        # tests/test_mcp_router.py's "test_second_catalog_source_requires_zero_
        # router_or_client_code_changes" as proof -- that test does not exist.
        # The real proof for the CLIENT-dispatch layer is
        # tests/test_mcp_client_catalog_dispatch.py::
        # test_second_catalog_source_requires_zero_client_code_changes, which
        # registers a completely fake second source and calls the unmodified
        # mcp_client.call_tool() -- a genuine test, but it covers dispatch, not
        # routing. mcp_router.py's own Section 6 tests (tests/test_mcp_router.py)
        # only ever exercise "prometheus" -- no fake second source is routed
        # through it. The "zero router code changes" half of this claim is
        # currently an inference from reading mcp_router.py's generic branch
        # (no source name hardcoded there), not something a dedicated test
        # proves. Add that test before treating this as fully proven.
        "primary_tool":      "query_range",
        "query_field":       "promql",
        "query_field_hint":  "a valid PromQL expression targeting the named pod/namespace where possible",
        "query_limits": {
            "max_window_seconds": 3600,  # 1 hour max range per query
            "max_result_series":  50,
            "timeout_s":          10,
        },
    },
}


def get_enabled_sources_for_cluster(cluster_id: str) -> Dict[str, Dict[str, Any]]:
    """Returns {source_id: entry} for every catalog entry that is BOTH enabled
    AND authorized for cluster_id (empty allowed_clusters = authorized for any
    cluster once enabled). Catalog PRESENCE alone is never authorization —
    only this function's return value is."""
    result = {}
    for source_id, entry in SOURCE_CATALOG.items():
        if not entry.get("enabled"):
            continue
        allowed = entry.get("allowed_clusters") or []
        if allowed and cluster_id not in allowed:
            continue
        result[source_id] = entry
    return result


# Bounded, explicit keyword table — matches ONLY against the fixed capability
# vocabulary above, not open-ended text classification. A phrase not in this
# table returns None, and the router falls through to the existing
# Kubernetes-only deterministic path unchanged.
_CAPABILITY_KEYWORDS = (
    (("historical metric", "cpu usage over", "memory usage over", "metric trend",
      "usage trend", "usage over time", "over the last hour", "over the past hour"),
     CAPABILITY_HISTORICAL_METRICS),
    (("deployment history", "rollout history", "recent change", "who changed",
      "change history"),
     CAPABILITY_DEPLOYMENT_CHANGES),
)


def capability_needed(task_plan: str, primary_gap: str) -> Optional[str]:
    """Returns the first capability from the fixed vocabulary whose keywords
    appear in task_plan/primary_gap, or None. None means: no additional
    source is relevant to what the planner asked for — the router should not
    even look at the catalog for this step."""
    text = f"{task_plan or ''} {primary_gap or ''}".lower()
    for keywords, capability in _CAPABILITY_KEYWORDS:
        if any(k in text for k in keywords):
            return capability
    return None


def select_additional_source(cluster_id: str, task_plan: str, primary_gap: str) -> Optional[Dict[str, Any]]:
    """The router-facing entry point. Returns the single best-matching enabled,
    authorized source entry (with its own source_id under "source_id") for this
    cluster + evidence gap, or None if nothing applies -- covering both "no
    capability need was expressed" and "a capability is needed but no enabled
    source provides it" (the caller is responsible for turning the latter into
    an explicit evidence gap, never a fabricated result).

    Deterministic, not LLM-driven: with today's catalog holding at most one
    real candidate per capability, a plain best-match is sufficient and avoids
    spending an extra LLM call choosing between options that don't exist yet.
    If a second source is ever added for the SAME capability, disambiguating
    between them is a real design question for that addition, not solved here
    speculatively — agent/prompts.py's dormant MCP_ROUTER_PHASE1_SYSTEM/USER
    prompts already sketch the LLM-driven shape that decision would take.
    """
    needed = capability_needed(task_plan, primary_gap)
    if needed is None:
        return None
    candidates = get_enabled_sources_for_cluster(cluster_id)
    for source_id, entry in candidates.items():
        if needed in entry.get("capabilities", frozenset()):
            return {**entry, "source_id": source_id, "matched_capability": needed}
    return None
