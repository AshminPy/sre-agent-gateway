"""Negative/control tests for the direct_support fix, 2026-08-29 -- required before approving
the fix per the review's own gating (docs/management/confidence-genericity-review-2026-08-28.md
#15.5): letting supported_inference claims earn direct_support credit must not become a new
false-high mechanism. Each test runs the REAL score_root_cause_confidence() end to end, not an
isolated unit.

Four properties required, in the reviewer's own words:
  - well-supported causal inference scores appropriately (high)
  - unsupported/plausible inference stays low
  - contradictory evidence stays low
  - lexical overlap alone cannot create high confidence
"""
from agent.confidence.claim_builder import build_claims
from agent.confidence.policy import POLICY
from agent.confidence.scorer import score_root_cause_confidence

from tests.conftest import CLUSTER, make_evidence


def _rcc(claims, evidence_store, incident_type="Unknown", resolved_context=None):
    return score_root_cause_confidence(
        claims=claims, contradictions=[], hypotheses=[],
        evidence_store=evidence_store, tool_history=[],
        resolved_context=resolved_context or {"cluster_name": CLUSTER},
        incident_type=incident_type, policy=POLICY,
    )


def test_well_supported_causal_inference_scores_high():
    """A genuine causal claim, specifically and directly backed by its cited evidence, must
    now be able to reach a HIGH root_cause_confidence -- not capped low purely because a real
    root cause is, by nature, an inference over observed facts."""
    evidence_store = {
        "ev_001": make_evidence(
            "ev_001", "describe_pod_detail",
            summary="pod status", key_facts=["pod ImagePullBackOff container bad-image"],
        ),
        "ev_002": make_evidence(
            "ev_002", "list_events",
            summary="events", key_facts=["Failed to pull image nonexistent-image manifest unknown"],
        ),
    }
    claims = build_claims({
        "claims": [{
            "text": "The ImagePullBackOff is caused by the nonexistent-image tag not "
                    "existing in the registry, per the manifest unknown error.",
            "claim_type": "supported_inference",
            "supporting_evidence_ids": ["ev_001", "ev_002"],
        }],
    }, ["ev_001", "ev_002"], evidence_store)
    result = _rcc(claims, evidence_store)
    assert result["components"]["direct_support"] == 1.0
    assert result["score"] >= POLICY.band_thresholds["review"]


def test_unsupported_plausible_inference_stays_low():
    """A claim that SOUNDS plausible and uses correct-sounding vocabulary but whose specific
    mechanism is not actually in the cited evidence must not earn full direct_support --
    known residual limit (documented in test_claim_grounding_negation_and_context.py case 1)
    means this can still ground at the WORD-overlap level, so this test checks the level this
    fix CAN control: a claim citing evidence that shares no real content with it at all."""
    evidence_store = {
        "ev_001": make_evidence(
            "ev_001", "describe_pod_detail",
            summary="pod status", key_facts=["pod is Running"],
        ),
    }
    claims = build_claims({
        "claims": [{
            "text": "This outage was caused by a network partition between availability zones.",
            "claim_type": "supported_inference",
            "supporting_evidence_ids": ["ev_001"],
        }],
    }, ["ev_001"], evidence_store)
    result = _rcc(claims, evidence_store)
    assert result["components"]["direct_support"] < 0.5


def test_contradictory_evidence_stays_low():
    """An inference whose own cited evidence explicitly denies it must score near-zero
    direct_support -- the negation fix (claim_builder.py, same date) feeding this."""
    evidence_store = {
        "ev_001": make_evidence(
            "ev_001", "describe_pod_detail",
            summary="pod status", key_facts=["restart count is 0, no CrashLoopBackOff detected"],
        ),
    }
    claims = build_claims({
        "claims": [{
            "text": "The pod is in CrashLoopBackOff and has restarted repeatedly.",
            "claim_type": "supported_inference",
            "supporting_evidence_ids": ["ev_001"],
        }],
    }, ["ev_001"], evidence_store)
    result = _rcc(claims, evidence_store)
    assert result["components"]["direct_support"] == 0.0


def test_lexical_overlap_alone_cannot_create_high_confidence():
    """A claim sharing ONLY the investigation's own namespace/pod name with unrelated evidence
    (the contextual-generic fix, same date) must not reach a high overall score even though
    older behavior would have called it 'grounded' on lexical overlap alone."""
    resolved_context = {"cluster_name": CLUSTER, "namespace": "test-incidents", "pod": "target-pod"}
    evidence_store = {
        "ev_001": make_evidence(
            "ev_001", "describe_pod_detail",
            summary="ConfigMap app-config not found in test-incidents for pod target-pod",
            key_facts=["ConfigMap app-config missing"],
        ),
    }
    claims = build_claims({
        "claims": [{
            "text": "The Service selector for notification-svc in test-incidents does not "
                    "match pod target-pod's labels, causing zero endpoints.",
            "claim_type": "supported_inference",
            "supporting_evidence_ids": ["ev_001"],
        }],
    }, ["ev_001"], evidence_store, resolved_context)
    result = _rcc(claims, evidence_store, resolved_context=resolved_context)
    assert result["score"] < POLICY.band_thresholds["review"]
