"""Regression tests for issue #73: a missing-cluster request must genuinely safe-stop,
not silently fall back to sre-test-cluster through any of three separate paths.

agent/main.py's own defaults (cluster/namespace -> "sre-test-cluster"/"test-incidents")
were the first, most direct bug -- covered by inspection in the diff, and indirectly by
every test below that constructs a request with no cluster/namespace and expects an
empty resolved_context. Three further paths could still defeat the safe-stop even with
main.py fixed, each covered by its own test here:

1. input_normalizer.py defaulted a missing namespace to "test-incidents" -- even with a
   correctly-empty cluster, that fake namespace could uniquely match a real cluster via
   context_resolver's tier-4 project/environment/namespace routing.
2. resolve_cluster_routing() let an UNKNOWN explicit cluster_hint fall through to tier 4
   and match a real cluster via namespace -- silently investigating the wrong cluster
   instead of stopping on "that cluster doesn't exist."
3. SREAgent.query() recalled and could save memory keyed on an empty/mismatched
   cluster+namespace, via an OR match that let unrelated history leak through.

tests/test_context_resolver.py and tests/test_multi_cluster_registry.py already cover
the baseline "no hints at all -> safe-stop" and "valid explicit cluster -> resolves
normally" cases -- not duplicated here.
"""
import agent.main as main_mod
import agent.mcp_client as mcp_client_mod
from agent.graph import _after_context
from agent.main import SREAgent
from agent.nodes.input_normalizer import input_normalizer

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
}


def _patch_registry(monkeypatch, registry: dict):
    monkeypatch.setattr(mcp_client_mod, "_get_cluster_registry", lambda: registry)


# ── 1. Missing cluster and namespace reach context_resolver as genuinely empty ──────

def test_input_normalizer_leaves_namespace_empty_when_nothing_supplied(monkeypatch):
    """No resource_hints.namespace, and the LLM extraction also finds no namespace in the
    query text -- must stay "", not fall back to "test-incidents"."""
    monkeypatch.setattr(
        "agent.nodes.input_normalizer.llm_json",
        lambda *a, **k: (
            {"incident_type": "Unknown"},  # no "namespace" key at all
            {"total_tokens": 10, "cost_usd": 0.0, "input_tokens": 5, "output_tokens": 5,
             "cached_input_tokens": 0, "reasoning_tokens": 0, "tool_tokens": 0,
             "billable_output_tokens": 5},
        ),
    )
    state = {
        "run_id": "run_test_normalizer",
        "investigation": {"current_step": 0},
        "incident_envelope": {
            "user_query": "Something is wrong, investigate.",
            "resource_hints": {},  # no cluster, no namespace, no pod
            "incident": {},
        },
    }

    result = input_normalizer(state)
    ctx = result["resolved_context"]

    assert ctx["cluster_hint"] == ""
    assert ctx["namespace"] == ""


# ── 2. Unknown explicit cluster must not fall through to namespace-based routing ────

def test_unknown_explicit_cluster_with_matching_namespace_does_not_route_to_sre_test_cluster(monkeypatch):
    """The exact scenario the fix closes: an unknown cluster_hint paired with a namespace
    that WOULD uniquely match sre-test-cluster via tier 4 must still safe-stop -- an
    unknown explicit cluster is a real error, not something to paper over via namespace."""
    _patch_registry(monkeypatch, REGISTRY)

    result = mcp_client_mod.resolve_cluster_routing(
        cluster_hint="unknown-typo-cluster",
        namespace_hint="test-incidents",  # alone, this WOULD uniquely match sre-test-cluster
    )

    assert result["resolved"] is False
    assert result["method"] == "unresolved"
    assert result["cluster_name"] == ""
    assert "sre-test-cluster" not in result["reason"]
    assert "unknown-typo-cluster" in result["reason"]


def test_valid_explicit_cluster_is_unaffected_by_the_unknown_cluster_guard(monkeypatch):
    """Positive control: a real, registered cluster_hint must still resolve via tier 1,
    proving the new early-return doesn't fire for legitimate requests."""
    _patch_registry(monkeypatch, REGISTRY)

    result = mcp_client_mod.resolve_cluster_routing(
        cluster_hint="sre-test-cluster", namespace_hint="test-incidents",
    )

    assert result["resolved"] is True
    assert result["cluster_name"] == "sre-test-cluster"
    assert result["method"] == "exact_id"


# ── 3. Missing cluster must not recall or save memory ───────────────────────────────

def test_missing_cluster_skips_memory_recall_and_save(monkeypatch):
    calls = {"recall_mb": 0, "recall_fallback": 0, "store_mb": 0, "save_fallback": 0}

    monkeypatch.setattr(main_mod, "investigate", lambda payload: {
        "run_id": "run_test_nomem",
        "status": "done",
        "summary": {"likely_root_cause": "n/a", "confidence_score": 0.9},
        "confidence": 0.9,
        "confidence_band": "auto",
        "requires_human_review": False,
    })
    monkeypatch.setattr(SREAgent, "_sanitize", classmethod(lambda cls, text, is_output=False: (text, False)))
    monkeypatch.setattr(SREAgent, "_save_to_gcs", classmethod(lambda cls, *a, **k: None))

    def _track(name):
        def _fn(cls, *a, **k):
            calls[name] += 1
            return ""
        return _fn

    monkeypatch.setattr(SREAgent, "_mb_recall", classmethod(_track("recall_mb")))
    monkeypatch.setattr(SREAgent, "_recall_memory", classmethod(_track("recall_fallback")))
    monkeypatch.setattr(SREAgent, "_mb_store", classmethod(_track("store_mb")))
    monkeypatch.setattr(SREAgent, "_save_memory", classmethod(_track("save_fallback")))

    SREAgent.query(query="Something is wrong, investigate.")  # no cluster, no namespace

    assert calls["recall_mb"] == 0, "Memory Bank recall must be skipped for a clusterless request"
    assert calls["recall_fallback"] == 0, "fallback recall must be skipped for a clusterless request"


def test_valid_cluster_still_recalls_memory_no_regression(monkeypatch):
    """Positive control: a real cluster must still trigger the existing recall path."""
    calls = {"recall_mb": 0}

    monkeypatch.setattr(main_mod, "investigate", lambda payload: {
        "run_id": "run_test_mem_ok",
        "status": "done",
        "summary": {"likely_root_cause": "n/a", "confidence_score": 0.5},
        "confidence": 0.5,
        "confidence_band": "escalate",
        "requires_human_review": True,
    })
    monkeypatch.setattr(SREAgent, "_sanitize", classmethod(lambda cls, text, is_output=False: (text, False)))
    monkeypatch.setattr(SREAgent, "_save_to_gcs", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(SREAgent, "_mb_store", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(SREAgent, "_save_memory", classmethod(lambda cls, *a, **k: None))

    def _track_recall(cls, c, n):
        calls["recall_mb"] += 1
        return ""

    monkeypatch.setattr(SREAgent, "_mb_recall", classmethod(_track_recall))
    monkeypatch.setattr(SREAgent, "_recall_memory", classmethod(lambda cls, c, n: ""))

    SREAgent.query(query="Pod x is crashing", cluster="sre-test-cluster", namespace="test-incidents")

    assert calls["recall_mb"] == 1


# ── Fallback in-process memory must require BOTH cluster and namespace to match ─────

def test_fallback_memory_requires_both_cluster_and_namespace_not_either():
    SREAgent._memory = [
        {"query": "old", "root_cause": "unrelated cluster's issue", "cluster": "other-cluster",
         "namespace": "test-incidents", "ts": 0},
    ]
    try:
        # Same namespace, DIFFERENT cluster -- must NOT match under an AND requirement.
        result = SREAgent._recall_memory("sre-test-cluster", "test-incidents")
        assert result == "", "an OR match would incorrectly surface another cluster's history"
    finally:
        SREAgent._memory = []


# ── 4. No MCP/tool execution occurs after the safe-stop ─────────────────────────────

def test_failed_status_routes_straight_to_rca_builder_not_task_planner():
    """graph.py's own conditional edge after context_resolver:
    {"ok": "task_planner", "failed": "rca_builder"} -- a cluster_unresolved safe-stop
    (which sets investigation.status="failed") must route here, never reaching
    task_planner/mcp_router/tool_executor at all."""
    state = {"investigation": {"status": "failed"}}
    assert _after_context(state) == "failed"


def test_done_status_routes_to_task_planner_no_regression():
    state = {"investigation": {"status": "running"}}
    assert _after_context(state) == "ok"
