"""End-to-end acceptance tests for the primary-causal-claim + independent-verifier
architecture (2026-09-01 confidence-architecture review), run through the REAL
agent.nodes.rca_builder.rca_builder() node — not just derive_outcome() in isolation.

Mocking boundaries (see tests/conftest.py's module-level comment for exactly why these
are two independently-patchable names, not one):
  - agent.nodes.rca_builder.llm_json  -> the RCA-generation call's response
  - agent.llm.llm_json                -> the verifier's call response (mock_verifier)
  - agent.confidence.verifier.read_evidence -> the verifier's source-evidence reads
    (mock_verifier_evidence)

Everything else (build_claims, select_primary_causal_claim, detect_contradictions,
score_investigation_completeness, derive_outcome) is the real production code.

Required acceptance cases (2026-09-01 review, final round):
  A. correct causal claim + strong source evidence -> CONFIRMED
  B. supported but incomplete causal claim -> PROBABLE/POSSIBLE
  C. unsupported plausible cause + strong surrounding facts -> INSUFFICIENT_EVIDENCE
  D. explicit abstention / "unknown" -> INSUFFICIENT_EVIDENCE
  E. supporting evidence + independent contradictory evidence -> CONFLICTING_EVIDENCE
     (via the VERIFIER's own Set A/Set B check -- see
     test_eval_scenario_matrix.py::test_conflicting_evidence_forces_conflicting_outcome
     for the separate DETERMINISTIC wrong_resource path)
  F. verifier failure/source evidence unavailable -> never CONFIRMED
  G. incomplete contradiction scan -> never CONFIRMED
  H. resource or temporal conflict -> CONFLICTING_EVIDENCE
  I. RCA + verifier token/cost totals include both calls
"""
from __future__ import annotations

import agent.nodes.rca_builder as rca_builder_mod
from agent.confidence.policy import POLICY
from agent.confidence.scorer import score_investigation_completeness
from tests.conftest import (
    CLUSTER, make_evidence, make_state, make_tool_history_entry,
    mock_verifier, mock_verifier_evidence,
)


def _quiet_observability_log(monkeypatch):
    monkeypatch.setattr(rca_builder_mod, "_write_observability_log", lambda *a, **k: None)


def _mock_rca_llm(monkeypatch, response: dict, usage: dict | None = None):
    usage = usage or {
        "input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 50,
        "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 150,
        "billable_output_tokens": 50, "cost_usd": 0.0001,
        "provider": "gemini", "model": "gemini-2.5-pro", "duration_s": 0.1,
    }
    monkeypatch.setattr(rca_builder_mod, "llm_json", lambda *a, **k: (dict(response), dict(usage)))


def _state_with_strong_evidence():
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["exit code 137"]),
        "ev_002": make_evidence("ev_002", "list_events", key_facts=["OOMKilled memory limit exceeded"]),
        "ev_003": make_evidence("ev_003", "get_previous_logs", key_facts=["killed out of memory"]),
    }
    tool_history = [
        make_tool_history_entry(0, "describe_pod_detail"),
        make_tool_history_entry(1, "list_events"),
        make_tool_history_entry(2, "get_previous_logs"),
    ]
    state = make_state("OOMKilled", evidence_store, tool_history)
    state["run_id"] = "run_test_e2e"
    state["incident_id"] = "inc_test_e2e"
    state["incident_envelope"] = {"user_query": "pod is OOMKilled", "memory_context": ""}
    state["sources_skipped"] = []
    state["investigation"]["completeness"] = score_investigation_completeness(state, POLICY)
    return state, evidence_store


# ── A. Correct causal claim + strong source evidence -> CONFIRMED ──────────────

def test_a_correct_claim_with_strong_evidence_reaches_confirmed(monkeypatch):
    _quiet_observability_log(monkeypatch)
    state, _ = _state_with_strong_evidence()
    _mock_rca_llm(monkeypatch, {
        "primary_causal_claim_index": 1,
        "claims": [{"text": "Container OOMKilled, exit code 137, memory limit exceeded",
                     "claim_type": "observed_fact", "supporting_evidence_ids": ["ev_001", "ev_002"]}],
        "alternative_hypotheses_considered": [], "evidence_chain": ["ev_001", "ev_002", "ev_003"],
        "evidence_gaps": [], "reasoning_trace": [], "suggested_remediation": ["Raise the memory limit."],
        "sources_skipped": [],
    })
    mock_verifier(monkeypatch)
    mock_verifier_evidence(monkeypatch)

    result = rca_builder_mod.rca_builder(state)["final_summary"]
    assert result["outcome"] == "confirmed"
    assert result["requires_human_review"] is False
    assert result["likely_root_cause"] == "Container OOMKilled, exit code 137, memory limit exceeded"


# ── B. Supported but incomplete causal claim -> PROBABLE/POSSIBLE ──────────────

def test_b_supported_but_low_completeness_reaches_probable(monkeypatch):
    _quiet_observability_log(monkeypatch)
    state, _ = _state_with_strong_evidence()
    state["investigation"]["completeness"] = {
        "score": 0.4, "band": "incomplete", "gaps": ["missing required evidence"],
        "evidence_domains_present": [], "missing_required_domains": [],
    }
    _mock_rca_llm(monkeypatch, {
        "primary_causal_claim_index": 1,
        "claims": [{"text": "Container OOMKilled, exit code 137", "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001", "ev_002"]}],
        "alternative_hypotheses_considered": [], "evidence_chain": ["ev_001", "ev_002"],
        "evidence_gaps": [], "reasoning_trace": [], "suggested_remediation": [], "sources_skipped": [],
    })
    mock_verifier(monkeypatch)
    mock_verifier_evidence(monkeypatch)

    result = rca_builder_mod.rca_builder(state)["final_summary"]
    assert result["outcome"] in ("probable", "possible")
    assert result["outcome"] != "confirmed"


def test_b_insufficient_sufficiency_with_supported_faithfulness_reaches_possible(monkeypatch):
    _quiet_observability_log(monkeypatch)
    state, _ = _state_with_strong_evidence()
    _mock_rca_llm(monkeypatch, {
        "primary_causal_claim_index": 1,
        "claims": [{"text": "Container OOMKilled, exit code 137", "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001", "ev_002"]}],
        "alternative_hypotheses_considered": [], "evidence_chain": ["ev_001", "ev_002"],
        "evidence_gaps": [], "reasoning_trace": [], "suggested_remediation": [], "sources_skipped": [],
    })
    mock_verifier(monkeypatch, sufficiency="insufficient")
    mock_verifier_evidence(monkeypatch)

    result = rca_builder_mod.rca_builder(state)["final_summary"]
    assert result["outcome"] == "possible"


# ── C. Unsupported plausible cause + strong surrounding facts -> INSUFFICIENT_EVIDENCE ──

def test_c_unsupported_primary_claim_with_strong_surrounding_facts_is_insufficient_evidence(monkeypatch):
    """The exact intermittent-001 regression: OTHER well-grounded observed_fact claims
    (the pod is healthy, 0 restarts) must never rescue an unsupported PRIMARY causal
    claim (the cause is external) up to CONFIRMED or PROBABLE."""
    _quiet_observability_log(monkeypatch)
    state, _ = _state_with_strong_evidence()
    _mock_rca_llm(monkeypatch, {
        "primary_causal_claim_index": 1,
        "claims": [
            {"text": "The cause is external to the pod", "claim_type": "supported_inference",
             "supporting_evidence_ids": ["ev_001"]},
            {"text": "The pod is Running with 0 restarts", "claim_type": "observed_fact",
             "supporting_evidence_ids": ["ev_002", "ev_003"]},
            {"text": "Logs show no errors", "claim_type": "observed_fact",
             "supporting_evidence_ids": ["ev_002", "ev_003"]},
        ],
        "alternative_hypotheses_considered": [], "evidence_chain": ["ev_001", "ev_002", "ev_003"],
        "evidence_gaps": [], "reasoning_trace": [], "suggested_remediation": [], "sources_skipped": [],
    })
    mock_verifier(monkeypatch, faithfulness="unsupported")
    mock_verifier_evidence(monkeypatch)

    result = rca_builder_mod.rca_builder(state)["final_summary"]
    assert result["outcome"] == "insufficient_evidence"
    assert result["outcome"] not in ("confirmed", "probable")


# ── D. Explicit abstention / "unknown" -> INSUFFICIENT_EVIDENCE ────────────────

def test_d_null_primary_index_reaches_insufficient_evidence(monkeypatch):
    """The exact selector-001/pending-001 regression: the model honestly says it can't
    name a specific cause (primary_causal_claim_index: null) -- must report
    insufficient_evidence, never confirmed, even with high completeness."""
    _quiet_observability_log(monkeypatch)
    state, _ = _state_with_strong_evidence()
    _mock_rca_llm(monkeypatch, {
        "primary_causal_claim_index": None,
        "claims": [{"text": "The root cause is unknown -- no relevant pod information found",
                     "claim_type": "observed_fact", "supporting_evidence_ids": ["ev_001"]}],
        "alternative_hypotheses_considered": [], "evidence_chain": ["ev_001"],
        "evidence_gaps": [], "reasoning_trace": [], "suggested_remediation": [], "sources_skipped": [],
    })
    # Deliberately NOT calling mock_verifier()/mock_verifier_evidence() -- the verifier
    # must never be invoked at all when there's no primary claim to verify (point 3,
    # final review round). If the real code called it anyway, this test would try a
    # real network call and fail/hang, catching the regression.

    result = rca_builder_mod.rca_builder(state)["final_summary"]
    assert result["outcome"] == "insufficient_evidence"
    assert result["requires_human_review"] is True
    assert result["primary_causal_claim_id"] is None
    assert result["verifier_result"] is None


# ── E. Independent contradictory evidence (verifier's OWN Set B check) -> CONFLICTING_EVIDENCE ──

def test_e_verifier_detects_contradiction_in_other_collected_evidence(monkeypatch):
    """Proves the verifier is genuinely independent of the RCA generator's own
    self-reported contradicting_evidence_ids (which is empty here) -- the contradiction
    is only found because the verifier itself inspects Set B."""
    _quiet_observability_log(monkeypatch)
    state, _ = _state_with_strong_evidence()
    _mock_rca_llm(monkeypatch, {
        "primary_causal_claim_index": 1,
        "claims": [{"text": "Container OOMKilled, exit code 137", "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001", "ev_002"]}],
        "alternative_hypotheses_considered": [],
        "evidence_chain": ["ev_001", "ev_002", "ev_003"],
        "evidence_gaps": [], "reasoning_trace": [], "suggested_remediation": [], "sources_skipped": [],
        # No contradicting_evidence_ids anywhere in this response -- the self-report
        # channel is silent.
    })
    mock_verifier(monkeypatch, semantic_contradiction="present", contradiction_evidence_ref="ev_003")
    mock_verifier_evidence(monkeypatch)

    result = rca_builder_mod.rca_builder(state)["final_summary"]
    assert result["outcome"] == "conflicting_evidence"
    assert result["verifier_result"]["semantic_contradiction"] == "present"
    assert result["verifier_result"]["contradiction_evidence_ref"] == "ev_003"


# ── F. Verifier failure / source evidence unavailable -> never CONFIRMED ──────

def test_f_verifier_llm_failure_never_reaches_confirmed(monkeypatch):
    _quiet_observability_log(monkeypatch)
    state, _ = _state_with_strong_evidence()
    _mock_rca_llm(monkeypatch, {
        "primary_causal_claim_index": 1,
        "claims": [{"text": "Container OOMKilled, exit code 137", "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001", "ev_002"]}],
        "alternative_hypotheses_considered": [], "evidence_chain": ["ev_001", "ev_002"],
        "evidence_gaps": [], "reasoning_trace": [], "suggested_remediation": [], "sources_skipped": [],
    })
    mock_verifier(monkeypatch, raise_exception=True)

    result = rca_builder_mod.rca_builder(state)["final_summary"]
    assert result["outcome"] not in ("confirmed", "probable")
    assert result["outcome"] == "insufficient_evidence"


def test_f_source_evidence_unavailable_never_reaches_confirmed(monkeypatch):
    _quiet_observability_log(monkeypatch)
    state, _ = _state_with_strong_evidence()
    _mock_rca_llm(monkeypatch, {
        "primary_causal_claim_index": 1,
        "claims": [{"text": "Container OOMKilled, exit code 137", "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001", "ev_002"]}],
        "alternative_hypotheses_considered": [], "evidence_chain": ["ev_001", "ev_002"],
        "evidence_gaps": [], "reasoning_trace": [], "suggested_remediation": [], "sources_skipped": [],
    })
    mock_verifier(monkeypatch)
    mock_verifier_evidence(monkeypatch, available_ids=[])  # nothing readable, incl. Set A

    result = rca_builder_mod.rca_builder(state)["final_summary"]
    assert result["outcome"] not in ("confirmed", "probable")


# ── G. Incomplete contradiction scan -> never CONFIRMED ────────────────────────

def test_g_incomplete_contradiction_scan_caps_below_confirmed(monkeypatch):
    _quiet_observability_log(monkeypatch)
    state, _ = _state_with_strong_evidence()
    _mock_rca_llm(monkeypatch, {
        "primary_causal_claim_index": 1,
        "claims": [{"text": "Container OOMKilled, exit code 137", "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001", "ev_002"]}],
        "alternative_hypotheses_considered": [], "evidence_chain": ["ev_001", "ev_002", "ev_003"],
        "evidence_gaps": [], "reasoning_trace": [], "suggested_remediation": [], "sources_skipped": [],
    })
    mock_verifier(monkeypatch)
    # ev_003 is "other" (Set B) evidence, deliberately made unreadable -- Set A (ev_001,
    # ev_002, the claim's own citations) stays fully available.
    mock_verifier_evidence(monkeypatch, available_ids=["ev_001", "ev_002"])

    result = rca_builder_mod.rca_builder(state)["final_summary"]
    assert result["outcome"] != "confirmed"
    assert result["verifier_result"]["contradiction_check_complete"] is False


# ── H. Resource or temporal conflict -> CONFLICTING_EVIDENCE ───────────────────

def test_h_temporal_conflict_reaches_conflicting_evidence(monkeypatch):
    _quiet_observability_log(monkeypatch)
    state, _ = _state_with_strong_evidence()
    _mock_rca_llm(monkeypatch, {
        "primary_causal_claim_index": 1,
        "claims": [{"text": "Container OOMKilled, exit code 137", "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001", "ev_002"]}],
        "alternative_hypotheses_considered": [], "evidence_chain": ["ev_001", "ev_002"],
        "evidence_gaps": [], "reasoning_trace": [], "suggested_remediation": [], "sources_skipped": [],
    })
    mock_verifier(monkeypatch, temporal_relevance="conflicting")
    mock_verifier_evidence(monkeypatch)

    result = rca_builder_mod.rca_builder(state)["final_summary"]
    assert result["outcome"] == "conflicting_evidence"


def test_h_deterministic_resource_mismatch_reaches_conflicting_evidence(monkeypatch):
    """Same as test_eval_scenario_matrix.py's equivalent test, kept here too so all H
    acceptance evidence lives in one file alongside its temporal sibling above."""
    _quiet_observability_log(monkeypatch)
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", cluster=CLUSTER, key_facts=["exit 137"]),
        "ev_002": make_evidence("ev_002", "list_events", cluster="some-other-cluster", key_facts=["evicted"]),
    }
    tool_history = [make_tool_history_entry(0, "describe_pod_detail"), make_tool_history_entry(1, "list_events")]
    state = make_state("OOMKilled", evidence_store, tool_history)
    state["run_id"] = "run_test_h"
    state["incident_id"] = "inc_test_h"
    state["incident_envelope"] = {"user_query": "pod is OOMKilled", "memory_context": ""}
    state["sources_skipped"] = []
    state["investigation"]["completeness"] = score_investigation_completeness(state, POLICY)

    _mock_rca_llm(monkeypatch, {
        "primary_causal_claim_index": 1,
        "claims": [{"text": "OOMKilled, exit 137", "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001", "ev_002"]}],
        "alternative_hypotheses_considered": [], "evidence_chain": ["ev_001", "ev_002"],
        "evidence_gaps": [], "reasoning_trace": [], "suggested_remediation": [], "sources_skipped": [],
    })
    mock_verifier(monkeypatch)
    mock_verifier_evidence(monkeypatch)

    result = rca_builder_mod.rca_builder(state)["final_summary"]
    assert result["outcome"] == "conflicting_evidence"
    assert any(c["kind"] == "wrong_resource" for c in result["contradictions"])


# ── I. RCA + verifier token/cost totals include both calls ─────────────────────

def test_i_investigation_totals_include_both_the_rca_call_and_the_verifier_call(monkeypatch):
    """Point 7, final review round: rca_builder.py's own accumulate_usage() call for its
    RCA-generation usage must not silently overwrite (rather than add to) the verifier's
    already-folded usage."""
    _quiet_observability_log(monkeypatch)
    state, _ = _state_with_strong_evidence()

    rca_usage = {
        "input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 500,
        "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 1500,
        "billable_output_tokens": 500, "cost_usd": 0.01,
        "provider": "gemini", "model": "gemini-2.5-pro", "duration_s": 1.0,
    }
    verifier_usage = {
        "input_tokens": 200, "cached_input_tokens": 0, "output_tokens": 100,
        "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 300,
        "billable_output_tokens": 100, "cost_usd": 0.002,
        "provider": "gemini", "model": "gemini-2.5-pro", "duration_s": 0.2,
    }
    _mock_rca_llm(monkeypatch, {
        "primary_causal_claim_index": 1,
        "claims": [{"text": "Container OOMKilled, exit code 137", "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001", "ev_002"]}],
        "alternative_hypotheses_considered": [], "evidence_chain": ["ev_001", "ev_002"],
        "evidence_gaps": [], "reasoning_trace": [], "suggested_remediation": [], "sources_skipped": [],
    }, usage=rca_usage)
    mock_verifier(monkeypatch, usage=verifier_usage)
    mock_verifier_evidence(monkeypatch)

    investigation = rca_builder_mod.rca_builder(state)["investigation"]
    # Both calls' usage must be present -- neither one silently overwritten by the other.
    assert investigation["tokens_total"] == rca_usage["total_tokens"] + verifier_usage["total_tokens"]
    assert round(investigation["estimated_cost_usd"], 6) == round(
        rca_usage["cost_usd"] + verifier_usage["cost_usd"], 6,
    )
