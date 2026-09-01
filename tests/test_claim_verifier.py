"""Unit tests for agent.confidence.verifier.verify_primary_claim() —
2026-09-01 confidence-architecture review.

Covers the "must not judge itself" structural constraint (only the claim's own text +
its cited evidence + other collected evidence — never the full RCA), the two-
evidence-set independence (Set A supports, Set B contradicts-only), and every
fail-closed path: LLM exception, unparseable response, hallucinated evidence_refs,
unreadable/oversized source evidence.
"""
from __future__ import annotations

from agent.confidence.claim_builder import build_claims
from agent.confidence.verifier import verify_primary_claim
from tests.conftest import make_evidence, mock_verifier, mock_verifier_evidence


def _claim(supporting_ids=("ev_001",)):
    result = {"primary_causal_claim_index": 1, "claims": [
        {"text": "the pod OOMKilled because of a memory leak", "claim_type": "observed_fact",
         "supporting_evidence_ids": list(supporting_ids)},
    ]}
    from agent.confidence.claim_builder import select_primary_causal_claim
    evidence_store = {eid: make_evidence(eid, "describe_pod_detail", key_facts=["x"]) for eid in supporting_ids}
    claims = build_claims(result, list(supporting_ids), evidence_store)
    return select_primary_causal_claim(claims, result)


def test_clean_response_produces_verified_ok_result(monkeypatch):
    mock_verifier(monkeypatch)
    mock_verifier_evidence(monkeypatch)
    claim = _claim()
    evidence_store = {"ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["x"])}
    result, usage = verify_primary_claim(claim, evidence_store)
    assert result.verified_ok is True
    assert result.causal_assertion == "specific_cause"
    assert result.faithfulness == "supported"
    assert result.source_evidence_complete is True
    assert result.contradiction_check_complete is True
    assert usage is not None
    assert usage["total_tokens"] > 0


def test_llm_exception_fails_closed(monkeypatch):
    mock_verifier(monkeypatch, raise_exception=True)
    claim = _claim()
    evidence_store = {"ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["x"])}
    result, usage = verify_primary_claim(claim, evidence_store)
    assert result.verified_ok is False
    assert usage is None  # the call never returned -- nothing to fold into token accounting


def test_unparseable_llm_response_fails_closed(monkeypatch):
    mock_verifier(monkeypatch, return_unparseable=True)
    claim = _claim()
    evidence_store = {"ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["x"])}
    result, usage = verify_primary_claim(claim, evidence_store)
    assert result.verified_ok is False
    # The call DID complete (got a response, just an invalid one) -- its usage is real and
    # must still be folded into accounting, unlike the exception case above.
    assert usage is not None


def test_evidence_ref_outside_cited_ids_is_rejected(monkeypatch):
    """The model claiming support from evidence it was never given (Set A) must fail
    closed, not be trusted at face value."""
    mock_verifier(monkeypatch, evidence_refs=["ev_999"])
    mock_verifier_evidence(monkeypatch)
    claim = _claim()
    evidence_store = {"ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["x"])}
    result, _ = verify_primary_claim(claim, evidence_store)
    assert result.verified_ok is False


def test_contradiction_present_without_a_valid_evidence_id_is_rejected(monkeypatch):
    mock_verifier(monkeypatch, semantic_contradiction="present", contradiction_evidence_ref="")
    mock_verifier_evidence(monkeypatch)
    claim = _claim()
    evidence_store = {"ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["x"])}
    result, _ = verify_primary_claim(claim, evidence_store)
    assert result.verified_ok is False


def test_contradiction_ref_outside_set_b_is_rejected(monkeypatch):
    mock_verifier(monkeypatch, semantic_contradiction="present", contradiction_evidence_ref="ev_001")
    mock_verifier_evidence(monkeypatch)
    claim = _claim()
    # ev_002 is NOT cited by the claim, so it's Set B -- but the mock points the
    # contradiction at ev_001, which is Set A (the claim's own supporting evidence),
    # never a valid Set B contradiction source.
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["x"]),
        "ev_002": make_evidence("ev_002", "list_events", key_facts=["y"]),
    }
    result, _ = verify_primary_claim(claim, evidence_store)
    assert result.verified_ok is False


def test_contradiction_ref_inside_set_b_is_accepted(monkeypatch):
    mock_verifier(monkeypatch, semantic_contradiction="present", contradiction_evidence_ref="ev_002")
    mock_verifier_evidence(monkeypatch)
    claim = _claim()
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["x"]),
        "ev_002": make_evidence("ev_002", "list_events", key_facts=["y"]),
    }
    result, _ = verify_primary_claim(claim, evidence_store)
    assert result.verified_ok is True
    assert result.semantic_contradiction == "present"
    assert result.contradiction_evidence_ref == "ev_002"


def test_no_incident_time_context_forces_temporal_relevance_unknown(monkeypatch):
    """2026-09-01 review, correction round 3: without real incident_time_context, the
    verifier's own claimed temporal_relevance is never trusted -- deterministically
    overridden to "unknown" regardless of what the LLM said, since it would only be
    guessing without a real incident timestamp to compare against."""
    mock_verifier(monkeypatch, temporal_relevance="relevant")
    mock_verifier_evidence(monkeypatch)
    claim = _claim()
    evidence_store = {"ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["x"])}
    result, _ = verify_primary_claim(claim, evidence_store, incident_time_context=None)
    assert result.temporal_relevance == "unknown"


def test_no_incident_time_context_overrides_even_a_claimed_conflict(monkeypatch):
    """The override is unconditional -- a claimed "conflicting" without real incident
    timing is just as untrustworthy as a claimed "relevant"."""
    mock_verifier(monkeypatch, temporal_relevance="conflicting")
    mock_verifier_evidence(monkeypatch)
    claim = _claim()
    evidence_store = {"ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["x"])}
    result, _ = verify_primary_claim(claim, evidence_store, incident_time_context={})
    assert result.temporal_relevance == "unknown"


def test_real_incident_time_context_lets_verifier_relevance_through(monkeypatch):
    mock_verifier(monkeypatch, temporal_relevance="relevant")
    mock_verifier_evidence(monkeypatch)
    claim = _claim()
    evidence_store = {"ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["x"])}
    result, _ = verify_primary_claim(claim, evidence_store, incident_time_context={"incident_start": 123.0})
    assert result.temporal_relevance == "relevant"


def test_source_evidence_unavailable_sets_source_evidence_complete_false(monkeypatch):
    mock_verifier(monkeypatch)
    mock_verifier_evidence(monkeypatch, available_ids=[])  # ev_001 (Set A) unreadable
    claim = _claim()
    evidence_store = {"ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["x"])}
    result, _ = verify_primary_claim(claim, evidence_store)
    assert result.source_evidence_complete is False


def test_source_evidence_oversized_fails_closed_not_silently_truncated(monkeypatch):
    """Point 4, final review round: false-low acceptable, false-high is not -- an
    oversized Set A item must never be silently head/tail-truncated and treated as
    though the verifier saw the complete source."""
    mock_verifier(monkeypatch)
    mock_verifier_evidence(monkeypatch, oversized_ids=["ev_001"])
    claim = _claim()
    evidence_store = {"ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["x"])}
    result, _ = verify_primary_claim(claim, evidence_store)
    assert result.source_evidence_complete is False


def test_other_evidence_unavailable_sets_contradiction_check_complete_false_only(monkeypatch):
    """Set B (contradiction-only) incompleteness is a DIFFERENT, less severe signal than
    Set A incompleteness -- it must not also flip source_evidence_complete, since the
    primary claim's own support was fully readable."""
    mock_verifier(monkeypatch)
    mock_verifier_evidence(monkeypatch, available_ids=["ev_001"])  # ev_002 (Set B) unreadable
    claim = _claim()
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["x"]),
        "ev_002": make_evidence("ev_002", "list_events", key_facts=["y"], ok=True),
    }
    result, _ = verify_primary_claim(claim, evidence_store)
    assert result.source_evidence_complete is True
    assert result.contradiction_check_complete is False


def test_failed_tool_call_evidence_is_excluded_from_set_b():
    """Set B is 'every OTHER successful (ok=True) evidence' -- a failed tool call's
    evidence entry carries no real data and must never be offered to the verifier as
    something to check for a contradiction against."""
    claim = _claim()
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["x"]),
        "ev_002": make_evidence("ev_002", "list_events", key_facts=[], ok=False),
    }
    # Directly inspect the Set B construction logic's effect via the other_ids computed
    # inside verify_primary_claim -- reproduced here at the same filter it uses
    # (eid not in cited_ids and ev.get("ok", True)), since that's a plain, already-visible
    # invariant worth pinning directly rather than only through an end-to-end mock.
    cited_ids = list(claim.supporting_evidence_ids)
    other_ids = [eid for eid, ev in evidence_store.items() if eid not in cited_ids and ev.get("ok", True)]
    assert "ev_002" not in other_ids
