"""Regression test for the evidence_extractor -> scorer field-name contract (issue #60).

Unlike tests/test_scorer.py, this does NOT use tests/conftest.py's make_evidence() fixture —
that fixture fabricates evidence with a "tool" key directly, which is exactly why the real bug
(evidence_extractor writing "source" instead of "tool") was invisible to every existing test.
This test runs the REAL evidence_extractor() node and feeds its REAL output into the REAL
scorer, so a future field-name drift between the two would fail here even if every other test
still uses fixtures that happen to match the scorer's expectations.
"""
import agent.nodes.evidence_extractor as evidence_extractor_mod
from agent.confidence.evidence_domains import EvidenceDomain, classify_tool
from agent.confidence.scorer import _evidence_domains_present

from tests.conftest import make_state


def _mock_io(monkeypatch, extracted: dict):
    monkeypatch.setattr(evidence_extractor_mod, "write_evidence", lambda *a, **k: "gs://bucket/ev_001.json")
    monkeypatch.setattr(evidence_extractor_mod, "redact", lambda raw: raw)
    monkeypatch.setattr(
        evidence_extractor_mod, "llm_json",
        lambda *a, **k: (dict(extracted), {
            "input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5,
            "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 15,
            "billable_output_tokens": 5, "cost_usd": 0.0,
            "provider": "gemini", "model": "gemini-2.5-pro", "duration_s": 0.1,
        }),
    )


def _state_with_tool_result(tool: str, ok: bool = True, error: str | None = None) -> dict:
    state = make_state("OOMKilled", {}, [])
    state["latest_tool_result"] = {
        "ok": ok,
        "tool": tool,
        "mcp_source": "gke_remote_mcp",
        "result": {"status": "Running"} if ok else None,
        "error": error,
    }
    return state


def test_successful_evidence_is_classified_by_the_real_scorer_not_unknown(monkeypatch):
    """The core regression: evidence_extractor's real output, fed into the real scorer's domain
    classifier, must resolve to the tool's actual domain -- not UNKNOWN."""
    _mock_io(monkeypatch, {
        "resource_type": "pod", "resource_id": "test-incidents/test-pod",
        "summary": "Pod is running", "key_facts": ["status: Running"],
    })
    state = _state_with_tool_result("describe_pod_detail")

    result = evidence_extractor_mod.evidence_extractor(state)
    evidence_store = result["evidence_store"]
    ev_entry = evidence_store["ev_001"]

    # The actual field-name contract the scorer depends on.
    assert "tool" in ev_entry, "evidence_extractor must write the tool name under key 'tool'"
    assert ev_entry["tool"] == "describe_pod_detail"
    assert "source" not in ev_entry, "no code reads 'source' from evidence entries -- must not resurface"

    # issue #68: a real per-evidence collection timestamp must be captured.
    assert isinstance(ev_entry.get("collected_at"), float)

    domains = _evidence_domains_present(evidence_store, tool_history=[])
    assert domains["ev_001"] == EvidenceDomain.KUBERNETES_STATUS
    assert classify_tool(ev_entry["tool"]) != EvidenceDomain.UNKNOWN


def test_failed_tool_call_evidence_is_also_classified_correctly(monkeypatch):
    """The error path (latest.get('ok') is False) has its own separate ev_entry construction --
    must carry the same fix."""
    _mock_io(monkeypatch, {})  # unused on the error path, evidence_extractor returns before llm_json
    state = _state_with_tool_result("list_events", ok=False, error="Forbidden")

    result = evidence_extractor_mod.evidence_extractor(state)
    ev_entry = result["evidence_store"]["ev_001"]

    assert ev_entry["tool"] == "list_events"
    assert "source" not in ev_entry
    assert classify_tool(ev_entry["tool"]) == EvidenceDomain.KUBERNETES_EVENTS
    assert isinstance(ev_entry.get("collected_at"), float)  # issue #68: error path too


def test_full_investigation_completeness_score_reflects_real_evidence_domains(monkeypatch):
    """End-to-end: three real evidence_extractor() calls covering OOMKilled's three required
    domains must score as complete via the real scorer -- proving the whole chain, not just the
    field name in isolation."""
    from agent.confidence.policy import POLICY
    from agent.confidence.scorer import score_investigation_completeness

    state = make_state("OOMKilled", {}, [])
    for i, tool in enumerate(["describe_pod_detail", "list_events", "get_previous_logs"], start=1):
        _mock_io(monkeypatch, {
            "resource_type": "pod", "resource_id": "test-incidents/test-pod",
            "summary": f"evidence from {tool}", "key_facts": ["fact"],
        })
        state["latest_tool_result"] = {
            "ok": True, "tool": tool, "mcp_source": "gke_remote_mcp",
            "result": {"data": "x"}, "error": None,
        }
        update = evidence_extractor_mod.evidence_extractor(state)
        state["evidence_store"] = {**state["evidence_store"], **update["evidence_store"]}
        state["evidence_ids"] = state["evidence_ids"] + update["evidence_ids"]
        state["tool_history"] = state["tool_history"] + [
            {"step": i - 1, "tool": tool, "ok": True, "mcp_source": "gke_remote_mcp", "args": {}}
        ]

    result = score_investigation_completeness(state, POLICY)
    assert result["components"]["required_evidence_coverage"] == 1.0, (
        f"real evidence_extractor output should satisfy OOMKilled's required domains; "
        f"got gaps={result['gaps']}"
    )
    assert result["band"] == "complete"
