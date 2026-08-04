import pytest

from agent.confidence.policy import ConfidencePolicy, POLICY


def test_default_policy_validates_at_import_time():
    # POLICY.validate() already ran at module import (policy.py bottom) — re-running must
    # also succeed, proving the shipped default policy is internally consistent.
    POLICY.validate()


def test_completeness_weights_must_sum_to_one():
    bad = ConfidencePolicy(completeness_weights={"routing_confirmed": 0.5, "identity_confirmed": 0.3})
    with pytest.raises(ValueError, match="completeness_weights"):
        bad.validate()


def test_confidence_weights_must_sum_to_one():
    bad = ConfidencePolicy(confidence_weights={"direct_support": 0.9})
    with pytest.raises(ValueError, match="confidence_weights"):
        bad.validate()


def test_band_thresholds_review_must_be_below_auto():
    bad = ConfidencePolicy(band_thresholds={"auto": 0.5, "review": 0.7})
    with pytest.raises(ValueError, match="band_thresholds"):
        bad.validate()


def test_hard_caps_must_be_in_unit_interval():
    bad = ConfidencePolicy(max_score_missing_critical_evidence=1.5)
    with pytest.raises(ValueError, match="max_score_missing_critical_evidence"):
        bad.validate()


def test_unknown_incident_type_falls_back_to_default_requirement():
    req = POLICY.evidence_requirement_for("SomeFutureIncidentTypeNotYetSeen")
    assert req is POLICY.evidence_requirements["_default"]
    # Deliberately light — must not demand domain-specific evidence for a type it never
    # heard of, per "do not require unavailable future data sources today".
    assert len(req.required_now) <= 1


def test_known_incident_types_have_required_now_evidence():
    for t in ("OOMKilled", "ImagePullBackOff", "CrashLoopBackOff"):
        req = POLICY.evidence_requirement_for(t)
        assert len(req.required_now) > 0
