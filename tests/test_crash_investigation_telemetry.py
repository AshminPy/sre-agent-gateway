"""Tests for the crash-investigation observability fix (dashboard telemetry work,
2026-09-05). Before this fix, an investigation that crashed inside graph.invoke()/
graph.stream() before agent/nodes/rca_builder.py's own observability log ever ran
produced NO structured Cloud Logging entry at all — only a free-text stack trace —
which would undercount total investigations and overstate success rate in any
dashboard built on the structured log.

Covers exactly the four properties required before this fix could be trusted:
  A. a successful investigation still produces the normal structured event
  B. a failed/crashed investigation produces an error terminal event
  C. a logging failure does not alter/mask the original investigation failure
  D. no duplicate terminal event is produced for one run
"""
from __future__ import annotations

import time

import agent.main as main_mod
import agent.nodes.rca_builder as rca_builder_mod
from tests.conftest import make_evidence, make_state, make_tool_history_entry


# ── Shared fake Cloud Logging client — same pattern as tests/test_observability.py,
# duplicated (not imported) so this file has no cross-test-file coupling. ──────────

class _FakeLogger:
    def __init__(self, name: str, sink: list):
        self._name = name
        self._sink = sink

    def log_struct(self, entry: dict, severity: str = "INFO") -> None:
        self._sink.append({"logger": self._name, "entry": entry, "severity": severity})


class _FakeLoggingClient:
    def __init__(self, sink: list):
        self._sink = sink

    def logger(self, name: str) -> _FakeLogger:
        return _FakeLogger(name, self._sink)


def _patch_cloud_logging(monkeypatch) -> list:
    import google.cloud.logging as cloud_logging_pkg

    sink: list = []
    monkeypatch.setattr(cloud_logging_pkg, "Client", lambda *a, **k: _FakeLoggingClient(sink))
    return sink


def _mock_llm_json(monkeypatch):
    response = {
        "primary_cause": "Container exceeded its memory limit and was OOMKilled.",
        "confidence": "high",
        "claims": [{
            "text": "Container exceeded its memory limit and was OOMKilled.",
            "claim_type": "observed_fact",
            "supporting_evidence_ids": ["ev_001"],
        }],
        "alternative_hypotheses_considered": [],
        "evidence_chain": ["ev_001"],
        "evidence_gaps": [],
        "reasoning_trace": ["exit 137 observed"],
        "suggested_remediation": ["Increase memory limit"],
        "sources_skipped": [],
    }
    usage = {
        "input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 50,
        "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 150,
        "billable_output_tokens": 50, "cost_usd": 0.0001,
        "provider": "gemini", "model": "gemini-2.5-pro", "duration_s": 0.1,
    }
    monkeypatch.setattr(rca_builder_mod, "llm_json", lambda *a, **k: (dict(response), dict(usage)))


def _minimal_rca_state() -> dict:
    evidence_store = {"ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["exit 137"])}
    tool_history = [make_tool_history_entry(0, "describe_pod_detail")]
    state = make_state("OOMKilled", evidence_store, tool_history, started_at=time.time() - 5)
    state["run_id"] = "run_test_crash_telemetry"
    state["incident_id"] = "inc_test_crash_telemetry"
    state["working_theory"] = "Container OOMKilled"
    state["incident_envelope"] = {"user_query": "Pod x OOMKilled", "memory_context": ""}
    state["sources_skipped"] = []
    state["evaluation_ids"] = []
    state["errors"] = []
    state["resolved_context"]["cluster_routing_method"] = "exact_id"
    state["resolved_context"]["cluster_routing_reason"] = "cluster_hint matched canonical id exactly"
    state["investigation"]["completeness"] = {
        "score": 0.9, "band": "complete", "gaps": [],
        "evidence_domains_present": [], "missing_required_domains": [],
    }
    state["investigation"]["tokens_total"] = 0
    state["investigation"]["estimated_cost_usd"] = 0.0
    return state


# ── Test A — successful investigation still produces the normal structured event ──

def test_A_successful_investigation_produces_completion_terminal_event(monkeypatch):
    sink = _patch_cloud_logging(monkeypatch)
    _mock_llm_json(monkeypatch)

    state = _minimal_rca_state()
    rca_builder_mod.rca_builder(state)

    assert len(sink) == 1, "expected exactly one structured log entry on the success path"
    entry = sink[0]["entry"]
    assert sink[0]["logger"] == "sre-agent-investigations"
    assert entry["event_type"] == "sre_agent_run_terminal"
    assert entry["terminal_kind"] == "completion"
    assert entry["error_type"] is None
    assert entry["error"] is None
    assert entry["partial_metrics_available"] is True
    assert entry["run_id"] == "run_test_crash_telemetry"


# ── Tests B, C, D — crash path, via the real investigate() entry point ─────────────

class _RaisingGraph:
    """Fake graph whose invoke() always raises — stands in for a genuine crash
    somewhere inside LangGraph node execution, before rca_builder ever runs."""

    def __init__(self, exc: Exception):
        self._exc = exc

    def invoke(self, state, config=None):
        raise self._exc


class _SucceedingGraph:
    """Fake graph whose invoke() returns a valid final state — used to prove the
    crash-terminal-event is NOT written when the graph itself completed, even if
    something later in the same try block raises (test D)."""

    def __init__(self, final_state: dict):
        self._final_state = final_state

    def invoke(self, state, config=None):
        return self._final_state


def _valid_final_state():
    return {
        "run_id": "run_test_no_duplicate",
        "investigation": {"status": "done", "confidence": 0.9, "confidence_band": "auto"},
        "resolved_context": {},
        "errors": [],
        "evidence_ids": [],
        "tool_history": [],
        "evidence_store": {},
        "final_summary": {},
    }


def test_B_crashed_investigation_produces_error_terminal_event(monkeypatch):
    sink = _patch_cloud_logging(monkeypatch)
    monkeypatch.setattr(main_mod, "_get_graph", lambda: _RaisingGraph(RuntimeError("boom")))

    result = main_mod.investigate({"query": "Pod x OOMKilled", "cluster": "c", "namespace": "n"})

    assert result["status"] == "failed"
    assert result["error_type"] == "RuntimeError"

    assert len(sink) == 1, "expected exactly one crash-terminal event"
    entry = sink[0]["entry"]
    assert sink[0]["logger"] == "sre-agent-investigations"
    assert entry["event_type"] == "sre_agent_run_terminal"
    assert entry["terminal_kind"] == "crash"
    assert entry["status"] == "error"
    assert entry["error_type"] == "RuntimeError"
    assert entry["error"] == "boom"
    assert entry["loop_exit_reason"] == "runtime_exception"
    # Real, non-fabricated request-time context.
    assert entry["cluster_requested"] == "c"
    assert entry["namespace_requested"] == "n"


def test_B2_crash_metrics_are_unknown_not_fabricated_zero(monkeypatch):
    """The graph can genuinely execute tools / spend tokens / collect evidence
    internally, then raise before ever returning that state to the caller.
    Reporting 0 in that case would be a fabricated fact, not an honest
    "don't know" — and would silently drag down average tokens/cost/tool-calls
    for every dashboard aggregate. These four fields must be None/NULL, with
    partial_metrics_available=false marking the row as having no usable
    metrics, so a consuming view can exclude it from averages instead of
    treating a NULL as a real zero reading."""
    sink = _patch_cloud_logging(monkeypatch)
    monkeypatch.setattr(main_mod, "_get_graph", lambda: _RaisingGraph(RuntimeError("boom")))

    main_mod.investigate({"query": "Pod x OOMKilled", "cluster": "c", "namespace": "n"})

    entry = sink[0]["entry"]
    assert entry["tools_called"] is None
    assert entry["evidence_ids"] is None
    assert entry["tokens_total"] is None
    assert entry["estimated_cost_usd"] is None
    assert entry["partial_metrics_available"] is False


def test_C_telemetry_write_failure_does_not_mask_original_exception(monkeypatch):
    """If the crash-terminal-event write ITSELF raises (e.g. Cloud Logging
    unreachable), investigate() must still report the ORIGINAL RuntimeError —
    never a telemetry-layer error, and never silently succeed."""
    monkeypatch.setattr(main_mod, "_get_graph", lambda: _RaisingGraph(RuntimeError("original failure")))

    import google.cloud.logging as cloud_logging_pkg

    def _boom_client(*a, **k):
        raise ConnectionError("Cloud Logging unreachable")

    monkeypatch.setattr(cloud_logging_pkg, "Client", _boom_client)

    result = main_mod.investigate({"query": "Pod x OOMKilled", "cluster": "c", "namespace": "n"})

    assert result["status"] == "failed"
    assert result["error_type"] == "RuntimeError"
    assert result["error"] == "original failure"


def test_D_no_duplicate_terminal_event_when_graph_already_completed(monkeypatch):
    """If the graph itself completes successfully but something AFTER it raises
    (e.g. inside _finalize_investigation_result), no crash-terminal event must be
    written — rca_builder already wrote the real one as part of the graph run."""
    sink = _patch_cloud_logging(monkeypatch)
    monkeypatch.setattr(main_mod, "_get_graph", lambda: _SucceedingGraph(_valid_final_state()))

    def _boom_finalize(result, started_at, payload):
        raise RuntimeError("finalize blew up")

    monkeypatch.setattr(main_mod, "_finalize_investigation_result", _boom_finalize)

    result = main_mod.investigate({"query": "Pod x OOMKilled", "cluster": "c", "namespace": "n"})

    assert result["status"] == "failed"
    assert result["error_type"] == "RuntimeError"
    # The graph completed -- _graph_completed suppressed the crash-terminal write.
    assert len(sink) == 0, "no crash-terminal event should be written once the graph itself completed"
