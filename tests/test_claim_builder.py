from agent.confidence.claim_builder import (
    build_claims, build_hypotheses, detect_contradictions, normalize_remediation_items,
)
from agent.confidence.models import ClaimType, Claim

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


def test_claim_sharing_only_generic_domain_word_does_not_get_full_credit():
    # issue #65: claim and evidence both mention "container" but describe unrelated
    # situations (image pull failure vs liveness-probe restarts) -- sharing one
    # generic K8s word must not be treated the same as a real, specific match. Note
    # _keywords() only matches words of 4+ letters, so "pod" (3 letters) never
    # overlaps on its own -- "container" is used here instead.
    evidence_store = {
        "ev_001": make_evidence("ev_001", "get_current_logs",
                                  summary="container repeatedly restarts due to failed liveness probe checks",
                                  key_facts=["liveness probe failed", "container restarted"]),
    }
    rca_result = {
        "claims": [{
            "text": "The container cannot pull its image from the registry",
            "claim_type": "observed_fact",
            "supporting_evidence_ids": ["ev_001"],
        }],
    }
    claims = build_claims(rca_result, ["ev_001"], evidence_store)
    assert claims[0].grounding_status == "weak_overlap"
    assert claims[0].support_strength == 0.4


def test_claim_sharing_a_specific_term_beyond_generic_words_is_fully_grounded():
    # Same generic word ("node") shared, but ALSO a specific, matching detail
    # (disk pressure) -- this should still get full credit.
    evidence_store = {
        "ev_001": make_evidence("ev_001", "get_current_logs",
                                  summary="node disk pressure caused pods to be evicted",
                                  key_facts=["node disk pressure", "pods evicted"]),
    }
    rca_result = {
        "claims": [{
            "text": "Node disk pressure is causing pods on this node to be evicted",
            "claim_type": "observed_fact",
            "supporting_evidence_ids": ["ev_001"],
        }],
    }
    claims = build_claims(rca_result, ["ev_001"], evidence_store)
    assert claims[0].grounding_status == "grounded"
    assert claims[0].support_strength == 1.0


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


# ── normalize_remediation_items() — Section 7, 2026-09-08 ──────────────────

_FAKE_CLAIM = Claim(
    claim_id="claim_001", text="OOMKilled", claim_type=ClaimType.OBSERVED_FACT,
    supporting_evidence_ids=["ev_001"],
)


def test_string_item_with_no_verified_cause_is_diagnostic_not_remediation():
    """No primary_claim (None) means no confirmed root cause exists yet -- an item can't
    honestly claim to address one, regardless of what the model's own text sounds like."""
    items = normalize_remediation_items(
        {"suggested_remediation": ["Restart the pod."]}, None, {},
    )
    assert len(items) == 1
    assert items[0].tied_to_primary_cause is False
    assert items[0].item_type == "diagnostic_next_step"


def test_string_item_with_verified_cause_is_tied_remediation():
    items = normalize_remediation_items(
        {"suggested_remediation": ["Raise the memory limit."]}, _FAKE_CLAIM, {},
    )
    assert len(items) == 1
    assert items[0].tied_to_primary_cause is True
    assert items[0].item_type == "remediation"
    assert items[0].action == "Raise the memory limit."


def test_structured_item_fields_pass_through():
    items = normalize_remediation_items(
        {"suggested_remediation": [{
            "action": "Increase memory limit to 1Gi",
            "type": "remediation",
            "prerequisites": "Confirm no other pods are memory-constrained on this node",
            "affected_scope": "This deployment only",
            "expected_benefit": "Prevents future OOMKills at this workload's normal usage",
            "risk": "Slightly higher memory reservation, may affect node bin-packing",
            "recovery_verification": "Watch for OOMKilled restarts over the next hour",
            "rollback": "Revert the limit to its previous value",
        }]},
        _FAKE_CLAIM, {},
    )
    assert len(items) == 1
    item = items[0]
    assert item.prerequisites == "Confirm no other pods are memory-constrained on this node"
    assert item.affected_scope == "This deployment only"
    assert item.expected_benefit.startswith("Prevents future OOMKills")
    assert item.risk.startswith("Slightly higher memory")
    assert item.recovery_verification.startswith("Watch for OOMKilled")
    assert item.rollback == "Revert the limit to its previous value"
    assert item.tied_to_primary_cause is True


def test_model_self_labeled_diagnostic_step_is_never_tied_even_with_a_verified_cause():
    """The model itself can correctly say an item is just a diagnostic next step, even
    when a root cause WAS confirmed -- e.g. 'also check X for a possible contributing
    factor.' Must be respected, not forced to remediation just because a cause exists."""
    items = normalize_remediation_items(
        {"suggested_remediation": [{
            "action": "Also check the node's disk pressure as a possible contributing factor.",
            "type": "diagnostic_next_step",
        }]},
        _FAKE_CLAIM, {},
    )
    assert items[0].tied_to_primary_cause is False
    assert items[0].item_type == "diagnostic_next_step"


def test_empty_action_items_are_skipped_not_crashed_on():
    items = normalize_remediation_items(
        {"suggested_remediation": ["", {"action": "  "}, {"no_action_key": True}, 42, None]},
        _FAKE_CLAIM, {},
    )
    assert items == []


def test_non_list_suggested_remediation_returns_empty_not_crashes():
    assert normalize_remediation_items({"suggested_remediation": "not a list"}, _FAKE_CLAIM, {}) == []
    assert normalize_remediation_items({}, _FAKE_CLAIM, {}) == []


def test_identifier_warning_fires_for_unrecognized_named_resource():
    items = normalize_remediation_items(
        {"suggested_remediation": ["Restart pod totally-different-pod-xyz to clear the issue."]},
        _FAKE_CLAIM, {"namespace": "prod", "pod": "checkout-service-abc123"},
    )
    assert "totally-different-pod-xyz" in items[0].identifier_warning
    assert "doesn't match any resource" in items[0].identifier_warning


def test_identifier_warning_absent_when_resource_matches():
    items = normalize_remediation_items(
        {"suggested_remediation": ["Restart pod checkout-service-abc123 to clear the issue."]},
        _FAKE_CLAIM, {"namespace": "prod", "pod": "checkout-service-abc123"},
    )
    assert items[0].identifier_warning == ""


def test_identifier_warning_absent_for_generic_action_with_no_resource_mention():
    """Must not false-positive on ordinary text that never names a specific resource."""
    items = normalize_remediation_items(
        {"suggested_remediation": ["Increase the memory limit and monitor for one hour."]},
        _FAKE_CLAIM, {"namespace": "prod", "pod": "checkout-service-abc123"},
    )
    assert items[0].identifier_warning == ""
