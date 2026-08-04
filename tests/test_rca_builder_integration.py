"""End-to-end unit tests for the rca_builder node with the LLM call mocked — verifies schema
backward compatibility and the full old-vs-new field set, without a real Gemini call.
"""
import agent.nodes.rca_builder as rca_builder_mod
from tests.conftest import make_evidence, make_state, make_tool_history_entry


def _mock_llm_json(monkeypatch, response: dict, usage: dict | None = None):
    usage = usage or {"tokens_input": 100, "tokens_output": 50, "tokens_total": 150, "cost_usd": 0.0001}
    monkeypatch.setattr(rca_builder_mod, "llm_json", lambda *a, **k: (dict(response), dict(usage)))
    # Cloud Logging isn't available in the test environment — _write_observability_log already
    # fails silently (try/except) if the client can't be constructed, so no mock needed there.


def _base_state(evidence_store, tool_history):
    state = make_state("OOMKilled", evidence_store, tool_history)
    state["run_id"] = "run_test_001"
    state["incident_id"] = "inc_test_001"
    state["working_theory"] = "Container OOMKilled"
    state["incident_envelope"] = {"user_query": "Pod x OOMKilled", "memory_context": ""}
    state["sources_skipped"] = []
    state["investigation"]["completeness"] = {
        "score": 0.9, "band": "complete", "gaps": [],
        "evidence_domains_present": [], "missing_required_domains": [],
    }
    state["investigation"]["tokens_total"] = 0
    state["investigation"]["estimated_cost_usd"] = 0.0
    return state


def test_final_summary_has_all_legacy_fields_downstream_consumers_read(monkeypatch):
    """run_eval.py reads summary['likely_root_cause'], ['confidence_score'], ['confidence_band']
    — these must exist with the right types after this rewrite."""
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["exit 137"]),
        "ev_002": make_evidence("ev_002", "list_events", key_facts=["OOMKilled"]),
        "ev_003": make_evidence("ev_003", "get_previous_logs", key_facts=["killed"]),
    }
    tool_history = [
        make_tool_history_entry(0, "describe_pod_detail"),
        make_tool_history_entry(1, "list_events"),
        make_tool_history_entry(2, "get_previous_logs"),
    ]
    state = _base_state(evidence_store, tool_history)

    _mock_llm_json(monkeypatch, {
        "incident_summary": "OOMKilled pod x",
        "likely_root_cause": "Container OOMKilled, exit code 137 (ev_001, ev_002)",
        "claims": [{
            "text": "Container OOMKilled, exit code 137",
            "claim_type": "observed_fact",
            "supporting_evidence_ids": ["ev_001", "ev_002"],
        }],
        "alternative_hypotheses_considered": [],
        "evidence_chain": ["ev_001", "ev_002"],
        "evidence_gaps": [],
        "reasoning_trace": ["pod status shows exit 137", "events confirm OOMKilling"],
        "suggested_remediation": ["Increase memory limit"],
        "sources_skipped": [],
    })

    result = rca_builder_mod.rca_builder(state)
    summary = result["final_summary"]

    # Legacy fields — must exist, must be the right type.
    assert isinstance(summary["likely_root_cause"], str) and summary["likely_root_cause"]
    assert isinstance(summary["confidence_score"], float)
    assert summary["confidence_band"] in ("auto", "review", "escalate")
    assert isinstance(summary["requires_human_review"], bool)
    assert summary["evidence_chain"] == ["ev_001", "ev_002", "ev_003"] or set(
        summary["evidence_chain"]) == {"ev_001", "ev_002", "ev_003"}

    # New fields — must also exist.
    assert summary["schema_version"] == "2.0"
    assert summary["confidence_deprecated"] is True
    assert "outcome" in summary
    assert "investigation_completeness" in summary
    assert "root_cause_confidence" in summary
    assert isinstance(summary["claims"], list) and summary["claims"]
    assert isinstance(summary["contradictions"], list)
    assert isinstance(summary["hypotheses"], list)
    assert summary["policy_version"]

    # investigation dict — legacy confidence/confidence_band set by rca_builder, overwriting
    # whatever task_evaluator set during the loop.
    inv_update = result["investigation"]
    assert inv_update["confidence"] == summary["confidence_score"]
    assert inv_update["confidence_band"] == summary["confidence_band"]


def test_strong_evidence_produces_higher_confidence_than_weak_evidence(monkeypatch):
    """Old-vs-new sanity check: strong, grounded, multi-domain evidence must score meaningfully
    higher than a single ungrounded guess — proving the new scorer actually discriminates."""
    strong_evidence = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["exit 137"]),
        "ev_002": make_evidence("ev_002", "list_events", key_facts=["OOMKilled"]),
        "ev_003": make_evidence("ev_003", "get_previous_logs", key_facts=["killed"]),
    }
    strong_history = [
        make_tool_history_entry(0, "describe_pod_detail"),
        make_tool_history_entry(1, "list_events"),
        make_tool_history_entry(2, "get_previous_logs"),
    ]
    strong_state = _base_state(strong_evidence, strong_history)
    _mock_llm_json(monkeypatch, {
        "likely_root_cause": "OOMKilled, exit 137 (ev_001, ev_002)",
        "claims": [{"text": "OOMKilled, exit 137", "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001", "ev_002"]}],
        "alternative_hypotheses_considered": [],
        "evidence_chain": ["ev_001", "ev_002"], "evidence_gaps": [],
        "reasoning_trace": [], "suggested_remediation": [], "sources_skipped": [],
    })
    strong_result = rca_builder_mod.rca_builder(strong_state)["final_summary"]

    weak_evidence = {"ev_001": make_evidence("ev_001", "get_current_logs", key_facts=["some log line"])}
    weak_history = [make_tool_history_entry(0, "get_current_logs")]
    weak_state = _base_state(weak_evidence, weak_history)
    weak_state["investigation"]["completeness"] = {
        "score": 0.3, "band": "incomplete", "gaps": ["missing required evidence"],
        "evidence_domains_present": [], "missing_required_domains": [],
    }
    _mock_llm_json(monkeypatch, {
        "likely_root_cause": "Possibly OOMKilled but unconfirmed",
        "claims": [{"text": "Possibly OOMKilled", "claim_type": "hypothesis",
                     "supporting_evidence_ids": []}],
        "alternative_hypotheses_considered": [],
        "evidence_chain": ["ev_001"], "evidence_gaps": ["no termination reason collected"],
        "reasoning_trace": [], "suggested_remediation": [], "sources_skipped": [],
    })
    weak_result = rca_builder_mod.rca_builder(weak_state)["final_summary"]

    assert strong_result["confidence_score"] > weak_result["confidence_score"]
    assert strong_result["outcome"] in ("confirmed", "probable")
    assert weak_result["outcome"] in ("insufficient_evidence", "unknown")


def test_no_evidence_forces_insufficient_evidence_outcome_and_zero_confidence(monkeypatch):
    state = _base_state({}, [])
    state["investigation"]["completeness"] = {
        "score": 0.0, "band": "incomplete", "gaps": ["no tool calls"],
        "evidence_domains_present": [], "missing_required_domains": [],
    }
    _mock_llm_json(monkeypatch, {
        "likely_root_cause": "unused — no_evidence path overrides this",
        "claims": [{"text": "unused", "claim_type": "hypothesis", "supporting_evidence_ids": []}],
    })
    result = rca_builder_mod.rca_builder(state)["final_summary"]
    assert result["confidence_score"] == 0.0
    assert result["confidence_band"] == "escalate"
    assert result["outcome"] == "insufficient_evidence"
    assert result["requires_human_review"] is True
