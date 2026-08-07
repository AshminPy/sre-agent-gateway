"""Unit tests for context_resolver.py's deterministic cluster routing.

Covers PRODUCTION-LAUNCH-PLAN.md Priority 5, gap (a): when no cluster is explicitly
provided, the resolver must safe-stop (errors + investigation.status == "failed") instead
of silently defaulting cluster_name to "sre-test-cluster".
"""
import agent.mcp_client as mcp_client_mod
from agent.nodes.context_resolver import context_resolver

REGISTRY = {
    "sre-test-cluster": {
        "canonical_id": "sre-test-cluster",
        "aliases": ["test-alias"],
        "project": "sreagent-test",
        "region": "us-central1",
        "cluster_type": "gke",
        "environment": "test",
        "allowed_namespaces": ["test-incidents"],
        "owner": "sre-team",
        "enabled": True,
        "mcp_primary": "gke_remote_mcp",
        "mcp_fallback": "k8s_mcp",
        "mcp_url": "",
    },
    "prod-cluster-us-east1": {
        "canonical_id": "prod-cluster-us-east1",
        "aliases": [],
        "project": "prj-prod",
        "region": "us-east1",
        "cluster_type": "gke",
        "environment": "production",
        "allowed_namespaces": [],
        "owner": "",
        "enabled": True,
        "mcp_primary": "gke_remote_mcp",
        "mcp_fallback": "k8s_mcp",
        "mcp_url": "",
    },
}


def _make_state(resolved_context: dict) -> dict:
    return {
        "run_id": "run_test_ctx",
        "resolved_context": resolved_context,
        "investigation": {"current_step": 0, "status": "running"},
    }


def _patch_registry(monkeypatch, registry: dict):
    monkeypatch.setattr(mcp_client_mod, "_get_cluster_registry", lambda: registry)


def test_missing_cluster_safe_stops_not_defaults_to_sre_test_cluster(monkeypatch):
    """Gap (a): no cluster_hint, no cluster_guess, no other hints — even though
    'sre-test-cluster' IS a valid, enabled entry in the registry, the resolver must NOT
    pick it. It must safe-stop instead."""
    _patch_registry(monkeypatch, REGISTRY)

    state = _make_state({
        "cluster_hint": "",
        "cluster_guess": "",
        "namespace": "",
        "project_hint": "",
        "environment_hint": "",
    })

    result = context_resolver(state)

    assert result["investigation"]["status"] == "failed"
    assert result["investigation"]["loop_exit_reason"] == "cluster_unresolved"
    assert result["errors"], "expected a non-empty errors list explaining the safe-stop"
    assert "sre-test-cluster" not in result["errors"][0]

    ctx_out = result["resolved_context"]
    assert "cluster_name" not in ctx_out, (
        "safe-stop path must not set a cluster_name at all — proves no guess was made"
    )
    assert ctx_out["cluster_explicitly_provided"] is False
    assert ctx_out["cluster_routing_method"] == "unresolved"


def test_missing_cluster_with_empty_registry_also_safe_stops(monkeypatch):
    """Same gap, but with an empty registry too — belt and suspenders."""
    _patch_registry(monkeypatch, {})

    state = _make_state({"cluster_hint": "", "cluster_guess": ""})
    result = context_resolver(state)

    assert result["investigation"]["status"] == "failed"
    assert "cluster_name" not in result["resolved_context"]


def test_ambiguous_namespace_across_two_clusters_safe_stops(monkeypatch):
    """Two enabled clusters both declare the same namespace — must not guess either one."""
    ambiguous_registry = {
        "cluster-a": {**REGISTRY["prod-cluster-us-east1"], "canonical_id": "cluster-a",
                      "allowed_namespaces": ["shared-ns"]},
        "cluster-b": {**REGISTRY["prod-cluster-us-east1"], "canonical_id": "cluster-b",
                      "allowed_namespaces": ["shared-ns"]},
    }
    _patch_registry(monkeypatch, ambiguous_registry)

    state = _make_state({"cluster_hint": "", "cluster_guess": "", "namespace": "shared-ns"})
    result = context_resolver(state)

    assert result["investigation"]["status"] == "failed"
    assert "cluster_name" not in result["resolved_context"]


def test_disabled_cluster_exact_match_safe_stops(monkeypatch):
    """An exact-id match against a disabled cluster must not be routed to."""
    registry = {
        "prod-cluster-us-east1": {**REGISTRY["prod-cluster-us-east1"], "enabled": False},
    }
    _patch_registry(monkeypatch, registry)

    state = _make_state({"cluster_hint": "prod-cluster-us-east1", "cluster_guess": ""})
    result = context_resolver(state)

    assert result["investigation"]["status"] == "failed"
    assert "cluster_name" not in result["resolved_context"]


def test_explicit_exact_id_hint_resolves_successfully(monkeypatch):
    """Positive control: an explicit, verified, exact cluster id resolves normally —
    proves the safe-stop tests above are actually exercising the "not provided" path,
    not a universal failure."""
    _patch_registry(monkeypatch, REGISTRY)

    state = _make_state({
        "cluster_hint": "prod-cluster-us-east1",
        "cluster_guess": "",
        "namespace": "test-incidents",
    })
    result = context_resolver(state)

    assert "investigation" not in result, "success path should not set investigation.status"
    ctx_out = result["resolved_context"]
    assert ctx_out["cluster_name"] == "prod-cluster-us-east1"
    assert ctx_out["cluster_explicitly_provided"] is True
    assert ctx_out["cluster_routing_method"] == "exact_id"


def test_approved_alias_resolves_successfully(monkeypatch):
    _patch_registry(monkeypatch, REGISTRY)

    state = _make_state({"cluster_hint": "test-alias", "cluster_guess": ""})
    result = context_resolver(state)

    assert "investigation" not in result
    ctx_out = result["resolved_context"]
    assert ctx_out["cluster_name"] == "sre-test-cluster"
    assert ctx_out["cluster_routing_method"] == "approved_alias"


def test_unapproved_alias_is_not_matched(monkeypatch):
    """A value that isn't an exact id and isn't a registered alias must not resolve —
    proves alias matching doesn't silently accept arbitrary strings."""
    _patch_registry(monkeypatch, REGISTRY)

    state = _make_state({"cluster_hint": "totally-made-up-cluster", "cluster_guess": ""})
    result = context_resolver(state)

    assert result["investigation"]["status"] == "failed"
    assert "cluster_name" not in result["resolved_context"]
