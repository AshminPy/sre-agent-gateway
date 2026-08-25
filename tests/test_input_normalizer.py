"""Unit tests for agent/nodes/input_normalizer.py — three behaviours the existing
suite never exercised.

tests/test_cluster_unresolved_safe_stop.py already covers the namespace side of this
node (a missing namespace must stay "" rather than defaulting to "test-incidents") and
is not duplicated here. What is covered below:

1. A missing user_query is a hard input error: the node returns an error and sets
   investigation.status="failed" BEFORE any model call — so no tokens are spent and
   graph.py's conditional edge routes straight to rca_builder.
2. CLI resource hints for pod and cluster always win over the LLM's extraction. For
   pod that means the hint replaces the extracted value; for cluster it means the
   hint becomes cluster_hint (the verified signal) while the LLM's guess is kept
   separately as cluster_guess and never merged into it.
3. incident_type falls back to "Unknown" when the LLM omits the key entirely.

llm_json is monkeypatched at agent.nodes.input_normalizer.llm_json — the same target
tests/test_cluster_unresolved_safe_stop.py uses — so no real model is ever called.
"""
import pytest

from agent.nodes.input_normalizer import input_normalizer

USAGE = {
    "total_tokens": 10, "cost_usd": 0.0, "input_tokens": 5, "output_tokens": 5,
    "cached_input_tokens": 0, "reasoning_tokens": 0, "tool_tokens": 0,
    "billable_output_tokens": 5,
}


def _patch_llm(monkeypatch, extracted: dict):
    """Replace the node's llm_json with a stub returning a fixed extraction."""
    monkeypatch.setattr(
        "agent.nodes.input_normalizer.llm_json",
        lambda *a, **k: (extracted, dict(USAGE)),
    )


def _forbid_llm(monkeypatch, calls: list):
    """Replace llm_json with a stub that records (and fails) if it is ever reached."""
    def _never(*a, **k):
        calls.append((a, k))
        raise AssertionError("llm_json must not be called when user_query is missing")

    monkeypatch.setattr("agent.nodes.input_normalizer.llm_json", _never)


def _state(envelope: dict) -> dict:
    return {
        "run_id": "run_test_input_normalizer",
        "investigation": {"current_step": 0},
        "incident_envelope": envelope,
    }


# ── 1. Missing user_query → error + investigation status failed ─────────────────────

@pytest.mark.parametrize("envelope", [
    {},                                                    # no envelope content at all
    {"resource_hints": {"cluster": "sre-test-cluster"}},   # hints present, query absent
    {"user_query": ""},                                    # explicitly empty
    {"user_query": None},                                  # explicitly null
], ids=["empty_envelope", "hints_but_no_query", "empty_string", "none"])
def test_missing_user_query_returns_error_and_failed_status(monkeypatch, envelope):
    """Every way a query can be absent must produce the same hard stop. The hints
    case matters on its own: resource_hints alone must never be treated as enough
    input to start an investigation."""
    calls = []
    _forbid_llm(monkeypatch, calls)

    result = input_normalizer(_state(envelope))

    assert result["errors"] == ["input_normalizer: user_query is required"]
    assert result["investigation"]["status"] == "failed"
    assert "resolved_context" not in result, "a failed input must not produce a context"
    assert calls == [], "the model must not be called for an invalid input"


def test_missing_incident_envelope_key_entirely_also_fails(monkeypatch):
    """state may not carry an incident_envelope key at all — the node reads it with
    .get() and must take the same failure path, not raise KeyError."""
    calls = []
    _forbid_llm(monkeypatch, calls)

    result = input_normalizer({"run_id": "run_test_no_envelope", "investigation": {"current_step": 0}})

    assert result["errors"] == ["input_normalizer: user_query is required"]
    assert result["investigation"]["status"] == "failed"
    assert calls == []


def test_present_user_query_does_not_fail_no_regression(monkeypatch):
    """Positive control: a real query must pass the guard and produce a context."""
    _patch_llm(monkeypatch, {"incident_type": "OOMKilled"})

    result = input_normalizer(_state({"user_query": "Pod x is crashing", "resource_hints": {}}))

    assert "errors" not in result
    assert result["resolved_context"]["incident_type"] == "OOMKilled"


# ── 2. CLI hints for pod and cluster always override the LLM ────────────────────────

def test_pod_hint_overrides_llm_extracted_pod(monkeypatch):
    _patch_llm(monkeypatch, {"incident_type": "CrashLoopBackOff", "pod": "llm-guessed-pod"})

    result = input_normalizer(_state({
        "user_query": "the api pod is crashing",
        "resource_hints": {"pod": "cli-pod-abc123"},
    }))

    assert result["resolved_context"]["pod"] == "cli-pod-abc123"


def test_cluster_hint_overrides_llm_and_llm_guess_stays_separate(monkeypatch):
    """cluster_hint is the verified signal (structured resource_hints from the caller);
    the LLM's free-text extraction is exposed only as cluster_guess. The hint must not
    be blended with, or replaced by, the guess."""
    _patch_llm(monkeypatch, {"incident_type": "OOMKilled", "cluster_name": "llm-guessed-cluster"})

    result = input_normalizer(_state({
        "user_query": "check the prod cluster",
        "resource_hints": {"cluster": "cli-cluster-xyz"},
    }))
    ctx = result["resolved_context"]

    assert ctx["cluster_hint"] == "cli-cluster-xyz"
    assert ctx["cluster_guess"] == "llm-guessed-cluster", "the LLM guess is kept, but separately"


def test_cluster_hint_wins_even_when_padded_with_whitespace(monkeypatch):
    """A hint arriving with surrounding whitespace is still a real hint — it must be
    stripped and still override, not fall through to the LLM's value."""
    _patch_llm(monkeypatch, {"incident_type": "OOMKilled", "cluster_name": "llm-guessed-cluster"})

    result = input_normalizer(_state({
        "user_query": "check the prod cluster",
        "resource_hints": {"cluster": "  cli-cluster-xyz  "},
    }))

    assert result["resolved_context"]["cluster_hint"] == "cli-cluster-xyz"


def test_pod_and_cluster_hints_override_together(monkeypatch):
    """Both hints supplied at once — the common CLI case."""
    _patch_llm(monkeypatch, {
        "incident_type": "OOMKilled", "pod": "llm-pod", "cluster_name": "llm-cluster",
    })

    result = input_normalizer(_state({
        "user_query": "investigate",
        "resource_hints": {"pod": "cli-pod", "cluster": "cli-cluster"},
    }))
    ctx = result["resolved_context"]

    assert ctx["pod"] == "cli-pod"
    assert ctx["cluster_hint"] == "cli-cluster"
    assert ctx["cluster_guess"] == "llm-cluster"


def test_llm_pod_is_used_when_no_pod_hint_supplied(monkeypatch):
    """Control for the override: without a CLI hint, the LLM's pod is what gets used —
    proving the assertions above come from the hint winning, not from the LLM value
    being ignored in general."""
    _patch_llm(monkeypatch, {"incident_type": "OOMKilled", "pod": "llm-guessed-pod"})

    result = input_normalizer(_state({"user_query": "investigate", "resource_hints": {}}))
    ctx = result["resolved_context"]

    assert ctx["pod"] == "llm-guessed-pod"
    assert ctx["cluster_hint"] == "", "no cluster hint means no cluster identity is asserted"


def test_empty_pod_and_cluster_hints_do_not_override(monkeypatch):
    """An empty-string hint is absent, not an override: pod falls back to the LLM value,
    and cluster_hint stays empty so context_resolver can safe-stop."""
    _patch_llm(monkeypatch, {"incident_type": "OOMKilled", "pod": "llm-guessed-pod"})

    result = input_normalizer(_state({
        "user_query": "investigate",
        "resource_hints": {"pod": "", "cluster": ""},
    }))
    ctx = result["resolved_context"]

    assert ctx["pod"] == "llm-guessed-pod"
    assert ctx["cluster_hint"] == ""


def test_pod_stays_empty_when_neither_hint_nor_llm_supplies_one(monkeypatch):
    _patch_llm(monkeypatch, {"incident_type": "OOMKilled"})

    result = input_normalizer(_state({"user_query": "investigate", "resource_hints": {}}))

    assert result["resolved_context"]["pod"] == ""


# ── 3. incident_type defaults to "Unknown" when the LLM omits it ────────────────────

def test_incident_type_defaults_to_unknown_when_llm_omits_it(monkeypatch):
    _patch_llm(monkeypatch, {"pod": "some-pod"})  # no "incident_type" key at all

    result = input_normalizer(_state({"user_query": "something is wrong", "resource_hints": {}}))

    assert result["resolved_context"]["incident_type"] == "Unknown"


def test_incident_type_defaults_to_unknown_for_an_empty_llm_extraction(monkeypatch):
    """The extraction can come back as a bare {} (a model returning no fields at all) —
    that must still yield a usable context, not a KeyError."""
    _patch_llm(monkeypatch, {})

    result = input_normalizer(_state({"user_query": "something is wrong", "resource_hints": {}}))

    assert result["resolved_context"]["incident_type"] == "Unknown"


def test_incident_type_from_llm_is_kept_when_present(monkeypatch):
    """Control: the default must not overwrite a real extracted incident_type."""
    _patch_llm(monkeypatch, {"incident_type": "ImagePullBackOff"})

    result = input_normalizer(_state({"user_query": "image will not pull", "resource_hints": {}}))

    assert result["resolved_context"]["incident_type"] == "ImagePullBackOff"
