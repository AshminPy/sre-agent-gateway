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
            {"tokens_input": 10, "tokens_output": 5, "tokens_total": 15, "cost_usd": 0.0},
        ),
    )

    state = _make_state("prod-cluster-us-east1")
    result = mcp_router(state)

    # Reached Phase 2 (LLM was called and returned "done") rather than safe-stopping —
    # no "cannot route safely" error present.
    assert not any("cannot route safely" in e for e in result.get("errors", []))
