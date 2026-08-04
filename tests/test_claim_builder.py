from agent.confidence.claim_builder import build_claims, build_hypotheses, detect_contradictions
from agent.confidence.models import ClaimType

from tests.conftest import CLUSTER, make_evidence


def test_grounded_claim_with_real_evidence_and_keyword_overlap():
    evidence_store = {
        "ev_001": make_evidence(
            "ev_001", "get_current_logs",
            summary="OOMKilled container exceeded memory limit",
            key_facts=["exit code 137", "OOMKilled", "memory limit 512Mi"],
        ),
    }
    rca_result = {
        "claims": [{
            "text": "Container was OOMKilled after exceeding its memory limit",
            "claim_type": "observed_fact",
            "supporting_evidence_ids": ["ev_001"],
        }],
    }
    claims = build_claims(rca_result, ["ev_001"], evidence_store)
    assert len(claims) == 1
    assert claims[0].grounding_status == "grounded"
    assert claims[0].support_strength == 1.0
    assert claims[0].claim_type == ClaimType.OBSERVED_FACT


def test_phantom_evidence_id_scores_zero_not_silently_accepted():
    evidence_store = {"ev_001": make_evidence("ev_001", "get_current_logs")}
    rca_result = {
        "claims": [{
            "text": "Something happened",
            "claim_type": "observed_fact",
            "supporting_evidence_ids": ["ev_999"],  # does not exist
        }],
    }
    claims = build_claims(rca_result, ["ev_001"], evidence_store)
    assert claims[0].grounding_status == "phantom_evidence"
    assert claims[0].support_strength == 0.0


def test_claim_with_no_supporting_evidence_is_ungrounded_and_worth_zero():
    evidence_store = {"ev_001": make_evidence("ev_001", "get_current_logs")}
    rca_result = {
        "claims": [{"text": "A cause with no citations", "claim_type": "hypothesis",
                     "supporting_evidence_ids": []}],
    }
    claims = build_claims(rca_result, ["ev_001"], evidence_store)
    assert claims[0].grounding_status == "ungrounded"
    assert claims[0].support_strength == 0.0


def test_claim_citing_real_evidence_with_no_keyword_overlap_is_flagged():
    evidence_store = {
        "ev_001": make_evidence("ev_001", "get_current_logs",
                                  summary="disk pressure eviction",
                                  key_facts=["node disk pressure", "pod evicted"]),
    }
    rca_result = {
        "claims": [{
            "text": "Authentication token expired during registry pull",
            "claim_type": "observed_fact",
            "supporting_evidence_ids": ["ev_001"],
        }],
    }
    claims = build_claims(rca_result, ["ev_001"], evidence_store)
    assert claims[0].grounding_status == "no_overlap"


def test_invalid_claim_type_from_model_falls_back_to_hypothesis_not_a_crash():
    evidence_store = {"ev_001": make_evidence("ev_001", "get_current_logs")}
    rca_result = {
        "claims": [{"text": "x", "claim_type": "definitely_true",  # not a real ClaimType
                     "supporting_evidence_ids": ["ev_001"]}],
    }
    claims = build_claims(rca_result, ["ev_001"], evidence_store)
    assert claims[0].claim_type == ClaimType.HYPOTHESIS


def test_legacy_shape_with_no_claims_field_falls_back_to_single_claim():
    """Old-shaped LLM response (just likely_root_cause, no claims[]) must not break."""
    evidence_store = {
        "ev_001": make_evidence("ev_001", "get_current_logs",
                                  summary="OOMKilled", key_facts=["OOMKilled", "exit 137"]),
    }
    rca_result = {"likely_root_cause": "OOMKilled — exit 137 (ev_001)"}
    claims = build_claims(rca_result, ["ev_001"], evidence_store)
    assert len(claims) == 1
    assert claims[0].claim_id == "claim_001"
    assert "ev_001" in claims[0].supporting_evidence_ids


def test_build_hypotheses_filters_unknown_evidence_ids():
    known = {"ev_001"}
    rca_result = {
        "alternative_hypotheses_considered": [{
            "description": "Maybe a node issue",
            "supporting_evidence_ids": ["ev_001", "ev_999"],
            "contradicting_evidence_ids": [],
            "status": "active",
        }],
    }
    hyps = build_hypotheses(rca_result, known)
    assert len(hyps) == 1
    assert hyps[0].supporting_evidence_ids == ["ev_001"]  # ev_999 filtered out, not fabricated


def test_build_hypotheses_invalid_status_defaults_to_active():
    hyps = build_hypotheses(
        {"alternative_hypotheses_considered": [{"description": "x", "status": "maybe"}]}, set(),
    )
    assert hyps[0].status == "active"


def test_detect_contradictions_wrong_cluster_is_deterministic_no_llm_needed():
    evidence_store = {
        "ev_001": make_evidence("ev_001", "get_current_logs", cluster="a-different-cluster"),
    }
    claims = build_claims(
        {"claims": [{"text": "x", "claim_type": "observed_fact",
                      "supporting_evidence_ids": ["ev_001"]}]},
        ["ev_001"], evidence_store,
    )
    resolved_context = {"cluster_name": CLUSTER}
    contradictions = detect_contradictions(claims, {}, evidence_store, resolved_context)
    assert len(contradictions) == 1
    assert contradictions[0].kind == "wrong_resource"


def test_detect_contradictions_llm_self_reported_semantic_contradiction():
    evidence_store = {
        "ev_001": make_evidence("ev_001", "get_current_logs"),
        "ev_002": make_evidence("ev_002", "list_events", summary="image pulled successfully"),
    }
    claims = build_claims(
        {"claims": [{"text": "Image pull auth failed", "claim_type": "observed_fact",
                      "supporting_evidence_ids": ["ev_001"],
                      "contradicting_evidence_ids": ["ev_002"]}]},
        ["ev_001", "ev_002"], evidence_store,
    )
    contradictions = detect_contradictions(claims, {}, evidence_store, {"cluster_name": CLUSTER})
    kinds = {c.kind for c in contradictions}
    assert "semantic" in kinds


def test_detect_contradictions_phantom_contradiction_reference_is_ignored():
    """Model flags a contradicting evidence ID that doesn't exist — must not fabricate a
    finding from a phantom reference."""
    evidence_store = {"ev_001": make_evidence("ev_001", "get_current_logs")}
    claims = build_claims(
        {"claims": [{"text": "x", "claim_type": "observed_fact",
                      "supporting_evidence_ids": ["ev_001"],
                      "contradicting_evidence_ids": ["ev_999"]}]},
        ["ev_001"], evidence_store,
    )
    contradictions = detect_contradictions(claims, {}, evidence_store, {"cluster_name": CLUSTER})
    assert contradictions == []
