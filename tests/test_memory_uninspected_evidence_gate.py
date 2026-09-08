"""Section 8 (2026-09-08): a confirmed root cause built on evidence that bypassed
Model Armor inspection (mcp/response_guard.py's fail-open path) must not be silently
promoted to trusted Memory Bank. Tests agent/main.py's
_primary_claim_cites_uninspected_evidence() gate directly.
"""
from agent.main import _primary_claim_cites_uninspected_evidence


def _result(primary_id, claims, evidence_store):
    return {
        "primary_causal_claim_id": primary_id,
        "claims": claims,
        "evidence_store": evidence_store,
    }


def test_no_primary_claim_never_blocks():
    assert _primary_claim_cites_uninspected_evidence(_result(None, [], {})) is False


def test_primary_claim_citing_fully_inspected_evidence_is_not_blocked():
    result = _result(
        "claim_001",
        [{"claim_id": "claim_001", "supporting_evidence_ids": ["ev_001"]}],
        {"ev_001": {"inspection_status": "inspected"}},
    )
    assert _primary_claim_cites_uninspected_evidence(result) is False


def test_primary_claim_citing_fail_open_evidence_is_blocked():
    result = _result(
        "claim_001",
        [{"claim_id": "claim_001", "supporting_evidence_ids": ["ev_001"]}],
        {"ev_001": {"inspection_status": "fail_open"}},
    )
    assert _primary_claim_cites_uninspected_evidence(result) is True


def test_unrelated_uninspected_evidence_not_cited_by_primary_claim_does_not_block():
    """A fail-open item elsewhere in the investigation that the confirmed claim never
    actually cites must not block promotion -- only the claim's OWN cited evidence
    matters, matching the docstring's stated scope."""
    result = _result(
        "claim_001",
        [{"claim_id": "claim_001", "supporting_evidence_ids": ["ev_001"]}],
        {"ev_001": {"inspection_status": "inspected"}, "ev_002": {"inspection_status": "fail_open"}},
    )
    assert _primary_claim_cites_uninspected_evidence(result) is False


def test_primary_claim_id_not_found_in_claims_does_not_crash():
    result = _result("claim_999", [{"claim_id": "claim_001", "supporting_evidence_ids": []}], {})
    assert _primary_claim_cites_uninspected_evidence(result) is False


def test_missing_evidence_store_entry_treated_as_inspected_not_a_crash():
    result = _result(
        "claim_001",
        [{"claim_id": "claim_001", "supporting_evidence_ids": ["ev_missing"]}],
        {},
    )
    assert _primary_claim_cites_uninspected_evidence(result) is False
