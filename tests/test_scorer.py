from agent.confidence.claim_builder import build_claims
from agent.confidence.models import Claim, ClaimType, Contradiction, Hypothesis, InvestigationOutcome
from agent.confidence.policy import POLICY
from agent.confidence.scorer import (
    confidence_band_from_scores,
    derive_outcome,
    score_investigation_completeness,
    score_root_cause_confidence,
)

from tests.conftest import CLUSTER, make_evidence, make_state, make_tool_history_entry


# ── Investigation Completeness ──────────────────────────────────────────────

def test_completeness_strong_oomkilled_evidence_scores_high():
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail",
                                  summary="pod status", key_facts=["exit code 137"]),
        "ev_002": make_evidence("ev_002", "list_events",
                                  summary="events", key_facts=["OOMKilling"]),
        "ev_003": make_evidence("ev_003", "get_previous_logs",
                                  summary="previous logs", key_facts=["killed"]),
    }
    tool_history = [
        make_tool_history_entry(0, "describe_pod_detail"),
        make_tool_history_entry(1, "list_events"),
        make_tool_history_entry(2, "get_previous_logs"),
    ]
    state = make_state("OOMKilled", evidence_store, tool_history)
    result = score_investigation_completeness(state, POLICY)
    assert result["score"] >= 0.85
    assert result["band"] == "complete"
    assert result["gaps"] == []


def test_completeness_four_duplicate_log_lines_not_treated_as_four_domains():
    """The exact regression this whole redesign targets: 4 evidence items from the SAME
    domain (current_logs) must NOT score as if 4 independent domains were covered."""
    evidence_store = {
        f"ev_{i:03d}": make_evidence(f"ev_{i:03d}", "get_current_logs", key_facts=[f"line {i}"])
        for i in range(1, 5)
    }
    tool_history = [make_tool_history_entry(i, "get_current_logs") for i in range(4)]
    state = make_state("OOMKilled", evidence_store, tool_history)  # requires 3 distinct domains
    result = score_investigation_completeness(state, POLICY)
    # Only 1 of 3 required domains (current_logs isn't even one of OOMKilled's required_now —
    # kubernetes_status/kubernetes_events/previous_logs are) actually present.
    assert result["components"]["required_evidence_coverage"] == 0.0
    assert result["band"] != "complete"
    assert "Missing required evidence domain" in result["gaps"][0]


def test_completeness_zero_tool_calls_scores_zero_tool_success():
    state = make_state("OOMKilled", {}, [])
    result = score_investigation_completeness(state, POLICY)
    assert result["components"]["tool_success"] == 0.0
    assert "No tool calls were attempted" in result["gaps"]


def test_completeness_failed_tool_calls_reduce_tool_success():
    evidence_store = {"ev_001": make_evidence("ev_001", "describe_pod_detail")}
    tool_history = [
        make_tool_history_entry(0, "describe_pod_detail", ok=True),
        make_tool_history_entry(1, "list_events", ok=False),
        make_tool_history_entry(2, "list_events", ok=False),
    ]
    state = make_state("OOMKilled", evidence_store, tool_history)
    result = score_investigation_completeness(state, POLICY)
    assert result["components"]["tool_success"] == pytest_approx(round(1 / 3, 3))


def pytest_approx(x):
    import pytest
    return pytest.approx(x, abs=1e-6)


def test_completeness_defaulted_cluster_scores_zero_routing_confirmed():
    state = make_state("OOMKilled", {"ev_001": make_evidence("ev_001", "describe_pod_detail")},
                        [make_tool_history_entry(0, "describe_pod_detail")],
                        cluster_explicitly_provided=False)
    result = score_investigation_completeness(state, POLICY)
    assert result["components"]["routing_confirmed"] == 0.0
    assert any("not explicitly provided" in g for g in result["gaps"])


def test_completeness_max_iterations_reached_before_coverage_penalizes_iteration_budget():
    evidence_store = {"ev_001": make_evidence("ev_001", "get_current_logs")}  # wrong domain, low coverage
    state = make_state("OOMKilled", evidence_store, [make_tool_history_entry(0, "get_current_logs")],
                        current_step=5, max_steps=5)
    result = score_investigation_completeness(state, POLICY)
    assert result["components"]["iteration_budget"] == 0.0


def test_completeness_unknown_incident_type_uses_light_default_requirement():
    evidence_store = {"ev_001": make_evidence("ev_001", "describe_pod_detail",
                                                key_facts=["status known"])}
    state = make_state("SomeBrandNewIncidentType", evidence_store,
                        [make_tool_history_entry(0, "describe_pod_detail")])
    result = score_investigation_completeness(state, POLICY)
    # Default requirement only needs kubernetes_status — should be fully covered.
    assert result["components"]["required_evidence_coverage"] == 1.0


# ── Root-Cause Confidence ────────────────────────────────────────────────

def _rcc(claims, contradictions=None, hypotheses=None, evidence_store=None, tool_history=None,
         incident_type="OOMKilled", resolved_context=None):
    return score_root_cause_confidence(
        claims=claims,
        contradictions=contradictions or [],
        hypotheses=hypotheses or [],
        evidence_store=evidence_store or {},
        tool_history=tool_history or [],
        resolved_context=resolved_context or {"cluster_name": CLUSTER},
        incident_type=incident_type,
        policy=POLICY,
    )


def test_root_cause_confidence_strong_grounded_multi_domain_evidence_scores_high():
    # All 3 of OOMKilled's required_now domains present (status, events, previous_logs) —
    # anything less triggers the missing-critical-evidence hard cap, tested separately below.
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail",
                                  summary="terminated OOMKilled", key_facts=["OOMKilled", "exit 137"]),
        "ev_002": make_evidence("ev_002", "list_events",
                                  summary="OOM events", key_facts=["OOMKilled"]),
        "ev_003": make_evidence("ev_003", "get_previous_logs",
                                  summary="previous container logs", key_facts=["OOM"]),
    }
    claims = build_claims({
        "claims": [{
            "text": "Container was OOMKilled, exit code 137",
            "claim_type": "observed_fact",
            "supporting_evidence_ids": ["ev_001", "ev_002", "ev_003"],
        }],
    }, ["ev_001", "ev_002", "ev_003"], evidence_store)
    result = _rcc(claims, evidence_store=evidence_store)
    assert result["score"] >= POLICY.band_thresholds["auto"]
    assert result["band"] == "high_confidence"


def test_root_cause_confidence_unsupported_claim_scores_zero():
    """LLM proposes a cause with zero supporting evidence — must not score positively."""
    claims = [Claim("claim_001", "Unsupported guess", ClaimType.HYPOTHESIS,
                     supporting_evidence_ids=[], grounding_status="ungrounded", support_strength=0.0)]
    result = _rcc(claims)
    assert result["score"] == 0.0
    assert result["band"] == "insufficient_evidence"


def test_root_cause_confidence_no_claims_at_all_is_insufficient_evidence():
    result = _rcc([])
    assert result["score"] == 0.0
    assert result["band"] == "insufficient_evidence"


def test_root_cause_confidence_contradiction_caps_score_even_with_strong_base():
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["exit 137"]),
        "ev_002": make_evidence("ev_002", "list_events", key_facts=["OOMKilled"]),
    }
    claims = build_claims({
        "claims": [{"text": "OOMKilled", "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001", "ev_002"]}],
    }, ["ev_001", "ev_002"], evidence_store)
    contradictions = [Contradiction("contra_001", "claim_001", "conflicting event",
                                     "ev_002", "", kind="semantic", severity=0.5)]
    result = _rcc(claims, contradictions=contradictions, evidence_store=evidence_store)
    assert result["score"] <= POLICY.max_score_unresolved_contradiction
    assert result["components"]["contradiction_penalty"] > 0


def test_root_cause_confidence_unresolved_active_hypothesis_caps_score():
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["exit 137"]),
        "ev_002": make_evidence("ev_002", "list_events", key_facts=["OOMKilled"]),
    }
    claims = build_claims({
        "claims": [{"text": "OOMKilled", "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001", "ev_002"]}],
    }, ["ev_001", "ev_002"], evidence_store)
    active_hyp = [Hypothesis("hyp_001", "Node memory pressure, not container limit",
                              supporting_evidence_ids=["ev_001"], status="active")]
    result = _rcc(claims, hypotheses=active_hyp, evidence_store=evidence_store)
    assert result["score"] <= POLICY.max_score_unresolved_hypothesis
    assert result["components"]["alternative_hypothesis_penalty"] > 0


def test_root_cause_confidence_eliminated_hypothesis_does_not_penalize():
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["exit 137"]),
        "ev_002": make_evidence("ev_002", "list_events", key_facts=["OOMKilled"]),
    }
    claims = build_claims({
        "claims": [{"text": "OOMKilled", "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001", "ev_002"]}],
    }, ["ev_001", "ev_002"], evidence_store)
    eliminated = [Hypothesis("hyp_001", "ruled out", status="eliminated")]
    result = _rcc(claims, hypotheses=eliminated, evidence_store=evidence_store)
    assert result["components"]["alternative_hypothesis_penalty"] == 0.0


def test_root_cause_confidence_missing_required_evidence_caps_score():
    evidence_store = {"ev_001": make_evidence("ev_001", "get_current_logs", key_facts=["some log"])}
    claims = build_claims({
        "claims": [{"text": "some cause", "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001"]}],
    }, ["ev_001"], evidence_store)
    # OOMKilled requires kubernetes_status/kubernetes_events/previous_logs — none present.
    result = _rcc(claims, evidence_store=evidence_store, incident_type="OOMKilled")
    assert result["score"] <= POLICY.max_score_missing_critical_evidence
    assert result["components"]["missing_evidence_penalty"] > 0


def test_root_cause_confidence_inference_claim_scores_lower_direct_support_than_observed_fact():
    evidence_store = {"ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["x"])}
    fact_claim = build_claims({"claims": [{"text": "x", "claim_type": "observed_fact",
                                             "supporting_evidence_ids": ["ev_001"]}]},
                                ["ev_001"], evidence_store)
    inference_claim = build_claims({"claims": [{"text": "x", "claim_type": "supported_inference",
                                                  "supporting_evidence_ids": ["ev_001"]}]},
                                     ["ev_001"], evidence_store)
    fact_result = _rcc(fact_claim, evidence_store=evidence_store)
    inference_result = _rcc(inference_claim, evidence_store=evidence_store)
    assert fact_result["components"]["direct_support"] > inference_result["components"]["direct_support"]


def test_root_cause_confidence_wrong_cluster_evidence_reduces_resource_identity_match():
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", cluster="other-cluster", key_facts=["x"]),
    }
    claims = build_claims({"claims": [{"text": "x", "claim_type": "observed_fact",
                                         "supporting_evidence_ids": ["ev_001"]}]},
                            ["ev_001"], evidence_store)
    result = _rcc(claims, evidence_store=evidence_store, resolved_context={"cluster_name": CLUSTER})
    assert result["components"]["resource_identity_match"] < 1.0


# ── Outcome derivation ────────────────────────────────────────────────────

def test_outcome_confirmed_requires_high_confidence_high_completeness_no_issues():
    completeness = {"score": 0.9}
    confidence = {"score": 0.9}
    outcome = derive_outcome(completeness, confidence, [], [], POLICY)
    assert outcome == InvestigationOutcome.CONFIRMED.value


def test_outcome_confirmed_downgrades_to_probable_with_unresolved_hypothesis():
    completeness = {"score": 0.9}
    confidence = {"score": 0.9}
    active_hyp = [Hypothesis("hyp_001", "x", supporting_evidence_ids=["ev_001"], status="active")]
    outcome = derive_outcome(completeness, confidence, [], active_hyp, POLICY)
    assert outcome != InvestigationOutcome.CONFIRMED.value


def test_outcome_conflicting_evidence_when_severe_contradiction_present():
    completeness = {"score": 0.9}
    confidence = {"score": 0.5}
    contradictions = [Contradiction("c1", "claim_001", "x", "ev_001", "", "semantic", severity=0.7)]
    outcome = derive_outcome(completeness, confidence, contradictions, [], POLICY)
    assert outcome == InvestigationOutcome.CONFLICTING_EVIDENCE.value


def test_outcome_insufficient_evidence_when_completeness_too_low():
    completeness = {"score": 0.2}
    confidence = {"score": 0.9}
    outcome = derive_outcome(completeness, confidence, [], [], POLICY)
    assert outcome == InvestigationOutcome.INSUFFICIENT_EVIDENCE.value


def test_outcome_unknown_when_confidence_very_low_but_completeness_ok():
    completeness = {"score": 0.9}
    confidence = {"score": 0.1}
    outcome = derive_outcome(completeness, confidence, [], [], POLICY)
    assert outcome == InvestigationOutcome.UNKNOWN.value


def test_outcome_never_confirmed_from_a_bare_numeric_threshold_alone():
    """Same confidence score (0.9), but WITH an unresolved contradiction — must not be
    CONFIRMED just because the number is high. This is the 'no auto-post on a numeric
    threshold alone' requirement."""
    completeness = {"score": 0.9}
    confidence = {"score": 0.9}
    contradictions = [Contradiction("c1", "claim_001", "x", "ev_001", "", "wrong_resource", severity=0.3)]
    outcome = derive_outcome(completeness, confidence, contradictions, [], POLICY)
    assert outcome != InvestigationOutcome.CONFIRMED.value


# ── Legacy confidence_band mapping ──────────────────────────────────────────

def test_confidence_band_mapping_matches_legacy_three_value_vocabulary():
    """iac/agent/monitoring.tf filters on jsonPayload.confidence_band with exactly these 3
    string values — this must never change without also updating that Terraform."""
    assert confidence_band_from_scores(InvestigationOutcome.CONFIRMED.value) == "auto"
    assert confidence_band_from_scores(InvestigationOutcome.PROBABLE.value) == "review"
    for outcome in (
        InvestigationOutcome.POSSIBLE.value,
        InvestigationOutcome.INSUFFICIENT_EVIDENCE.value,
        InvestigationOutcome.UNKNOWN.value,
        InvestigationOutcome.CONFLICTING_EVIDENCE.value,
    ):
        assert confidence_band_from_scores(outcome) == "escalate"


# ── Determinism ──────────────────────────────────────────────────────────

def test_same_input_always_produces_same_score():
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["exit 137"]),
        "ev_002": make_evidence("ev_002", "list_events", key_facts=["OOMKilled"]),
    }
    claims = build_claims({
        "claims": [{"text": "OOMKilled", "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001", "ev_002"]}],
    }, ["ev_001", "ev_002"], evidence_store)
    results = [_rcc(claims, evidence_store=evidence_store) for _ in range(5)]
    scores = {r["score"] for r in results}
    assert len(scores) == 1, "same structured input must always produce the same score"
