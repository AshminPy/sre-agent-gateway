"""Regression test for the main.py fail-open bug: a telemetry-formatting
failure inside _finalize_investigation_result() must never turn a successful
investigation into a reported failure. See
docs/management/observability-architecture-review-2026-08-23.md section 3.
"""
import agent.main as main_mod
from agent.main import investigate


class _UnserializableMarker:
    """Deliberately not JSON-serializable -- stands in for whatever bad value
    could slip into evidence_ids/summary/etc in production and break
    json.dumps(obs_event)."""

    def __repr__(self):
        return "<unserializable>"


class _RecordingGraph:
    def __init__(self, final_state):
        self._final_state = final_state

    def invoke(self, state, config=None):
        return self._final_state


def _fake_graph_state(evidence_ids):
    return {
        "run_id": "run_fail_open_test",
        "investigation": {"status": "done", "confidence": 0.9, "confidence_band": "auto"},
        "resolved_context": {},
        "errors": [],
        "evidence_ids": evidence_ids,
        "tool_history": [],
        "evidence_store": {},
        "final_summary": {},
    }


def test_investigate_survives_unserializable_observability_event(monkeypatch):
    """A successful investigation must still report status=done even when the
    observability event can't be JSON-serialized. Before the fix, obs_event's
    print(json.dumps(...)) ran unguarded inside the same try/except as the real
    investigation, so this exact failure would report status=failed instead."""
    bad_state = _fake_graph_state(evidence_ids=[_UnserializableMarker()])
    monkeypatch.setattr(main_mod, "_get_graph", lambda: _RecordingGraph(bad_state))

    result = investigate({"query": "Pod x OOMKilled", "cluster": "c", "namespace": "n"})

    assert result["status"] == "done"
    assert "error" not in result
    assert result["run_id"] == "run_fail_open_test"


def test_investigate_survives_bad_tool_history_duration(monkeypatch):
    """Same guarantee for a failure in the telemetry COMPUTATION itself (not just
    the print/flush_traces side effects) -- mcp_latency_s's sum() over
    tool_history's duration_s values raises if one entry has a non-numeric
    duration_s. Verifier-flagged gap: the original fix only guarded the
    print/flush_traces block, leaving this computation (main.py, right after the
    Priority 10 fields comment) inside the outer try/except that reports
    status=failed."""
    bad_state = _fake_graph_state(evidence_ids=["ev1"])
    bad_state["tool_history"] = [{"duration_s": "not-a-number"}]
    monkeypatch.setattr(main_mod, "_get_graph", lambda: _RecordingGraph(bad_state))

    result = investigate({"query": "Pod x OOMKilled", "cluster": "c", "namespace": "n"})

    assert result["status"] == "done"
    assert "error" not in result
    # Degraded-telemetry marker present since obs_event construction fell back.
    assert result["observability"].get("observability_degraded") is True


def test_investigate_survives_flush_traces_raising(monkeypatch):
    """Same guarantee if flush_traces() itself raises (e.g. exporter/network
    error) rather than the JSON serialization step."""
    good_state = _fake_graph_state(evidence_ids=["ev1"])
    monkeypatch.setattr(main_mod, "_get_graph", lambda: _RecordingGraph(good_state))

    import agent.otel as otel_mod

    def _boom(timeout_millis=5000):
        raise RuntimeError("exporter connection reset")

    monkeypatch.setattr(otel_mod, "flush_traces", _boom)

    result = investigate({"query": "Pod x OOMKilled", "cluster": "c", "namespace": "n"})

    assert result["status"] == "done"
    assert "error" not in result
