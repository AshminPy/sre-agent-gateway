"""Unit tests for mcp_router.py's deterministic MCP-source selection.

Covers PRODUCTION-LAUNCH-PLAN.md Priority 5, gap (b): an empty/missing cluster registry
entry must safe-stop the investigation (current_action tool == "done" + errors) instead of
silently defaulting cluster_type to "gke" and selected_mcp to "gke_remote_mcp".
"""
import agent.nodes.mcp_router as mcp_router_mod
from agent.nodes.mcp_router import mcp_router


def _make_state(cluster_name: str) -> dict:
    return {
        "run_id": "run_test_router",
        "resolved_context": {
            "cluster_name": cluster_name,
            "incident_type": "OOMKilled",
            "namespace": "test-incidents",
            "pod": "test-pod",
        },
        "investigation": {
            "current_step": 1,
            "task_plan": "check pod status",
            "primary_gap": "pod status unknown",
            "min_steps": 2,
        },
        "sources_skipped": [],
        "evidence_ids": [],
        "tool_history": [],
    }


def _fail_if_llm_called(monkeypatch):
    """Asserts the safe-stop happens BEFORE any LLM call — no wasted tokens on a
    cluster we already know we can't route to."""
    def _boom(*a, **k):
        raise AssertionError("llm_json must not be called when cluster routing safe-stops")
    monkeypatch.setattr(mcp_router_mod, "llm_json", _boom)


def test_empty_registry_safe_stops_not_gke_remote_mcp_default(monkeypatch):
    """Gap (b): cluster_name is set, but the registry is completely empty (e.g. GCS
    misconfigured or TTL refresh failed) — must safe-stop, not silently pick
    gke_remote_mcp."""
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: {})
    _fail_if_llm_called(monkeypatch)

    state = _make_state("prod-cluster-us-east1")
    result = mcp_router(state)

    assert result["current_action"]["tool"] == "done"
    assert result["current_action"]["mcp_source"] == "none"
    assert result.get("errors"), "expected a non-empty errors list explaining the safe-stop"
    assert "selected_mcp" not in result, (
        "safe-stop must not set selected_mcp/gke_remote_mcp at all"
    )


def test_cluster_missing_from_nonempty_registry_safe_stops(monkeypatch):
    """Registry has other clusters, just not this one — still must not guess."""
    registry = {
        "some-other-cluster": {
            "cluster_type": "custom", "enabled": True,
        },
    }
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: registry)
    _fail_if_llm_called(monkeypatch)

    state = _make_state("prod-cluster-us-east1")
    result = mcp_router(state)

    assert result["current_action"]["tool"] == "done"
    assert result["current_action"]["mcp_source"] == "none"
    assert result.get("errors")


def test_disabled_cluster_safe_stops(monkeypatch):
    """Cluster exists in the registry but is administratively disabled — must not route."""
    registry = {
        "prod-cluster-us-east1": {"cluster_type": "gke", "enabled": False},
    }
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: registry)
    _fail_if_llm_called(monkeypatch)

    state = _make_state("prod-cluster-us-east1")
    result = mcp_router(state)

    assert result["current_action"]["tool"] == "done"
    assert result["current_action"]["mcp_source"] == "none"
    assert result.get("errors")
    assert "disabled" in result["errors"][0].lower()


def test_empty_cluster_name_safe_stops(monkeypatch):
    """cluster_name never got resolved (context_resolver safe-stopped upstream) — this is
    the defense-in-depth path within mcp_router itself."""
    monkeypatch.setattr(
        mcp_router_mod, "_get_cluster_registry",
        lambda: {"prod-cluster-us-east1": {"cluster_type": "gke", "enabled": True}},
    )
    _fail_if_llm_called(monkeypatch)

    state = _make_state("")
    result = mcp_router(state)

    assert result["current_action"]["tool"] == "done"
    assert result["current_action"]["mcp_source"] == "none"
    assert result.get("errors")


def test_valid_enabled_cluster_proceeds_to_llm_routing(monkeypatch):
    """Positive control: a valid, enabled, registered cluster does NOT safe-stop and
    proceeds to Phase 2 tool selection — proves the safe-stop tests above are exercising
    the "unknown cluster" path specifically, not a universal failure."""
    registry = {
        "prod-cluster-us-east1": {"cluster_type": "gke", "enabled": True},
    }
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: registry)
    monkeypatch.setattr(
        mcp_router_mod, "llm_json",
        lambda *a, **k: (
            {"tool": "done", "arguments": {}, "reason": "no gap left"},
            {
                "input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5,
                "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 15,
                "billable_output_tokens": 5, "cost_usd": 0.0,
                "provider": "gemini", "model": "gemini-2.5-pro", "duration_s": 0.1,
            },
        ),
    )

    state = _make_state("prod-cluster-us-east1")
    result = mcp_router(state)

    # Reached Phase 2 (LLM was called and returned "done") rather than safe-stopping —
    # no "cannot route safely" error present.
    assert not any("cannot route safely" in e for e in result.get("errors", []))


def _mock_llm_returning(monkeypatch, tool: str, arguments: dict):
    """Common helper for the issue #246 tests below — the router's own LLM call
    proposes `tool`/`arguments`; we assert on what the AUTO-FILL step does to
    `arguments` afterward, not on the LLM's own (mocked) output."""
    monkeypatch.setattr(
        mcp_router_mod, "llm_json",
        lambda *a, **k: (
            {"tool": tool, "arguments": dict(arguments)},
            {
                "input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5,
                "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 15,
                "billable_output_tokens": 5, "cost_usd": 0.0,
                "provider": "gemini", "model": "gemini-2.5-pro", "duration_s": 0.1,
            },
        ),
    )


_CUSTOM_CLUSTER_REGISTRY = {
    "sre-lab": {"cluster_type": "custom", "enabled": True},
}


def test_issue_246_list_nodes_gets_no_namespace_argument(monkeypatch):
    """list_nodes() takes no namespace param at all (a Node isn't namespaced) — the
    router's auto-fill must not inject one, or the MCP server rejects the call with
    'unexpected_keyword_argument'."""
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: _CUSTOM_CLUSTER_REGISTRY)
    _mock_llm_returning(monkeypatch, "list_nodes", {})

    state = _make_state("sre-lab")
    result = mcp_router(state)

    assert result["current_action"]["tool"] == "list_nodes"
    assert result["current_action"]["mcp_source"] == "k8s_mcp"
    assert "namespace" not in result["current_action"]["arguments"]


def test_issue_246_explicit_namespace_from_model_is_stripped_not_just_unfilled(monkeypatch):
    """Expansion-review gap: the original fix only stopped the router's OWN auto-fill
    from ADDING a namespace to list_nodes/describe_node. It did nothing if the model's
    own raw arguments already included one (e.g. copied from a prior namespaced call
    in the same investigation) -- that would still reach the MCP server and fail with
    the same unexpected_keyword_argument error. Must be stripped either way."""
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: _CUSTOM_CLUSTER_REGISTRY)
    _mock_llm_returning(monkeypatch, "list_nodes", {"namespace": "test-incidents"})

    state = _make_state("sre-lab")
    result = mcp_router(state)

    assert result["current_action"]["tool"] == "list_nodes"
    assert "namespace" not in result["current_action"]["arguments"]


def test_issue_246_explicit_namespace_stripped_from_describe_node_too(monkeypatch):
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: _CUSTOM_CLUSTER_REGISTRY)
    _mock_llm_returning(monkeypatch, "describe_node", {"namespace": "test-incidents", "node_name": "gke-node-1"})

    state = _make_state("sre-lab")
    result = mcp_router(state)

    args = result["current_action"]["arguments"]
    assert "namespace" not in args
    assert args["node_name"] == "gke-node-1"


# ── Section 5: shared custom MCP requires an explicit cluster_id on every call ──

def test_cluster_id_is_auto_filled_for_k8s_mcp_calls(monkeypatch):
    """Every k8s_mcp tool call must carry cluster_id -- the shared custom MCP (one
    Cloud Run deployment serving multiple on-prem clusters, see mcp/server.py's
    resolve_cluster()) has no other way to know which cluster to connect to."""
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: _CUSTOM_CLUSTER_REGISTRY)
    _mock_llm_returning(monkeypatch, "list_pods", {})

    state = _make_state("sre-lab")
    result = mcp_router(state)

    assert result["current_action"]["arguments"]["cluster_id"] == "sre-lab"


def test_cluster_id_is_auto_filled_for_cluster_scoped_tools_too(monkeypatch):
    """list_nodes/describe_node get no namespace, but they DO still need cluster_id --
    it's not a namespace-shaped argument, it identifies which cluster's node list to
    read, which every custom-MCP tool needs regardless of namespace-scoping."""
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: _CUSTOM_CLUSTER_REGISTRY)
    _mock_llm_returning(monkeypatch, "list_nodes", {})

    state = _make_state("sre-lab")
    result = mcp_router(state)

    args = result["current_action"]["arguments"]
    assert args["cluster_id"] == "sre-lab"
    assert "namespace" not in args


def test_model_supplied_cluster_id_is_overridden_not_trusted(monkeypatch):
    """The model's own JSON response naming a DIFFERENT cluster_id than the one this
    investigation was actually resolved to must never be honored -- evidence text is
    untrusted input, and letting an LLM-controlled field pick the target cluster would
    reopen the cross-cluster-evidence risk this redesign closes (issue #86). The
    investigation's own resolved cluster_name always wins."""
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: _CUSTOM_CLUSTER_REGISTRY)
    _mock_llm_returning(monkeypatch, "list_pods", {"cluster_id": "some-other-cluster"})

    state = _make_state("sre-lab")
    result = mcp_router(state)

    assert result["current_action"]["arguments"]["cluster_id"] == "sre-lab"


def test_issue_246_describe_node_gets_no_namespace_argument(monkeypatch):
    """describe_node(node_name) takes only node_name — same cluster-scoped exclusion
    as list_nodes."""
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: _CUSTOM_CLUSTER_REGISTRY)
    _mock_llm_returning(monkeypatch, "describe_node", {"node_name": "gke-node-1"})

    state = _make_state("sre-lab")
    result = mcp_router(state)

    assert result["current_action"]["tool"] == "describe_node"
    args = result["current_action"]["arguments"]
    assert "namespace" not in args
    assert args["node_name"] == "gke-node-1"


def test_issue_246_normal_namespaced_tool_still_gets_namespace(monkeypatch):
    """Regression guard: the fix must not remove namespace auto-fill from tools that
    actually need it — only the two cluster-scoped exceptions."""
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: _CUSTOM_CLUSTER_REGISTRY)
    _mock_llm_returning(monkeypatch, "list_pods", {})

    state = _make_state("sre-lab")
    result = mcp_router(state)

    assert result["current_action"]["tool"] == "list_pods"
    assert result["current_action"]["arguments"]["namespace"] == "test-incidents"


def test_issue_246_pod_scoped_tool_still_gets_pod_name(monkeypatch):
    """Regression guard: the pod_name auto-fill (issue #72) is untouched by this fix."""
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: _CUSTOM_CLUSTER_REGISTRY)
    _mock_llm_returning(monkeypatch, "describe_pod_detail", {})

    state = _make_state("sre-lab")
    result = mcp_router(state)

    args = result["current_action"]["arguments"]
    assert args["namespace"] == "test-incidents"
    assert args["pod_name"] == "test-pod"


def test_issue_246_tool_not_in_allowlist_still_blocked_safely(monkeypatch):
    """Invalid/disallowed tool names must still fail safely (allowlist enforcement,
    unrelated to and unaffected by the namespace auto-fill fix)."""
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: _CUSTOM_CLUSTER_REGISTRY)
    _mock_llm_returning(monkeypatch, "delete_pod", {"namespace": "test-incidents"})

    state = _make_state("sre-lab")
    result = mcp_router(state)

    assert result["current_action"]["tool"] == "done"
    assert result["current_action"]["mcp_source"] == "none"
    assert any("not in allowlist" in e for e in result.get("errors", []))


def test_issue_246_routing_to_gke_remote_mcp_unchanged(monkeypatch):
    """Routing/mcp_source selection itself must be unaffected by this fix — a GKE
    cluster still routes to gke_remote_mcp regardless of the auto-fill change (which
    only applies to the k8s_mcp branch)."""
    monkeypatch.setattr(
        mcp_router_mod, "_get_cluster_registry",
        lambda: {"prod-cluster-us-east1": {"cluster_type": "gke", "enabled": True}},
    )
    _mock_llm_returning(monkeypatch, "list_k8s_events", {})

    state = _make_state("prod-cluster-us-east1")
    result = mcp_router(state)

    assert result["current_action"]["mcp_source"] == "gke_remote_mcp"
