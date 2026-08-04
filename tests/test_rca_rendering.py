"""Verifies the root-cause duplication bug is actually fixed, not just redesigned on paper."""
from agent.main import _build_executive_summary, _build_rca_report, _extract_root_cause


LONG_ROOT_CAUSE = (
    "The pod 'imagepull-pod' is in an ImagePullBackOff state because the specified image tag "
    "'nginx:1.2.3.4.5' was not found in the container registry (ev_003)."
)


def _summary(**overrides):
    base = {
        "likely_root_cause": LONG_ROOT_CAUSE,
        "confidence_band": "review",
        "outcome": "probable",
        "requires_human_review": True,
        "investigation_completeness": {"score": 0.8, "band": "complete_with_gaps", "gaps": []},
        "root_cause_confidence": {"score": 0.7, "band": "review_required", "reasons": []},
        "claims": [], "hypotheses": [], "contradictions": [],
        "policy_version": "1.0.0-uncalibrated",
    }
    base.update(overrides)
    return base


def test_extract_root_cause_is_the_single_source_both_renderers_use():
    summary = _summary()
    assert _extract_root_cause(summary) == LONG_ROOT_CAUSE


def test_executive_summary_does_not_repeat_the_full_root_cause_text():
    summary = _summary()
    exec_summary = _build_executive_summary(summary, {}, {"incident_type": "ImagePullBackOff"})
    # The full 180-char root cause must NOT appear verbatim in the executive summary — only
    # a short (<=~130 char) reference to it, per the fix.
    assert LONG_ROOT_CAUSE not in exec_summary
    assert "nginx:1.2.3.4.5" in exec_summary  # still identifiable, just truncated


def test_rca_report_contains_the_full_root_cause_text():
    summary = _summary()
    payload = {"cluster": "sre-test-cluster", "namespace": "test-incidents", "pod": "imagepull-pod",
               "severity": "high"}
    obs_event = {"run_id": "run_test", "cluster": "sre-test-cluster", "namespace": "test-incidents",
                 "pod": "imagepull-pod", "tools_called": 2, "evidence_count": 1, "latency_ms": 1000,
                 "tokens_total": 100, "estimated_cost_usd": 0.001}
    report = _build_rca_report(payload, summary, {"confidence_band": "review"}, {}, obs_event, ["ev_003"])
    assert "1.2.3.4.5" in report  # the specific detail survives into the full report


def test_executive_summary_and_rca_report_together_do_not_render_the_same_long_passage_twice():
    """The actual regression: both sections used to independently render the SAME
    350+-char root-cause text. Now only one section carries the full text."""
    summary = _summary()
    exec_summary = _build_executive_summary(summary, {}, {"incident_type": "ImagePullBackOff"})
    payload = {"cluster": "sre-test-cluster", "namespace": "test-incidents", "pod": "imagepull-pod",
               "severity": "high"}
    obs_event = {"run_id": "run_test", "cluster": "sre-test-cluster", "namespace": "test-incidents",
                 "pod": "imagepull-pod", "tools_called": 2, "evidence_count": 1, "latency_ms": 1000,
                 "tokens_total": 100, "estimated_cost_usd": 0.001}
    report = _build_rca_report(payload, summary, {"confidence_band": "review"}, {}, obs_event, ["ev_003"])

    # The RCA report word-wraps text across lines, so compare whitespace-normalized text
    # rather than a literal contiguous substring.
    normalized_report = " ".join(report.split())
    full_text_in_exec = LONG_ROOT_CAUSE in exec_summary
    full_text_in_report = LONG_ROOT_CAUSE in normalized_report
    assert not (full_text_in_exec and full_text_in_report), (
        "root cause full text rendered in BOTH sections — duplication bug is back"
    )
    assert full_text_in_report, "full root cause text must exist somewhere (the RCA report)"
