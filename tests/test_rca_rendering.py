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


def test_extract_root_cause_never_crashes_on_a_non_string_llm_response():
    """Regression test for the 2026-08-04 production incident: the LLM occasionally returned
    a non-string value for likely_root_cause under the more complex confidence-framework
    prompt, and slicing it directly (elsewhere in main.py, since fixed to reuse this function)
    raised 'unhashable type: slice'. _extract_root_cause must always return a string, whatever
    the LLM sends."""
    summary = _summary(likely_root_cause={"text": "malformed nested object", "type": "observed_fact"})
    result = _extract_root_cause(summary)
    assert isinstance(result, str)
    result[:300]  # must not raise — this is the exact operation that crashed in production


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


def test_rca_report_model_line_reflects_the_real_deployed_model_not_a_hardcoded_string(monkeypatch):
    """Regression for issue #62: the RCA report used to always print the literal string
    'Gemini 2.5 Flash', regardless of which model was actually deployed. main.py's
    _build_rca_report() does `from agent.llm import MODEL` fresh on every call (a local
    import inside the function body), so patching the agent.llm module attribute directly
    is sufficient -- no adapter reload/reinstantiation needed for this test."""
    import agent.llm

    monkeypatch.setattr(agent.llm, "MODEL", "gemini-2.5-pro")

    summary = _summary()
    payload = {"cluster": "sre-test-cluster", "namespace": "test-incidents", "pod": "imagepull-pod",
               "severity": "high"}
    obs_event = {"run_id": "run_test", "cluster": "sre-test-cluster", "namespace": "test-incidents",
                 "pod": "imagepull-pod", "tools_called": 2, "evidence_count": 1, "latency_ms": 1000,
                 "tokens_total": 100, "estimated_cost_usd": 0.001}
    report = _build_rca_report(payload, summary, {"confidence_band": "review"}, {}, obs_event, ["ev_003"])
    assert "gemini-2.5-pro" in report
    assert "Gemini 2.5 Flash" not in report


def test_rca_report_loop_count_reads_the_real_state_field():
    """Regression for issue #62: the report read inv['investigation_loops'] / inv['loop_count'],
    neither of which is ever set anywhere in AgentState -- always rendered 'Loops: 0'. The real
    field loop_controller.py/task_evaluator.py actually maintain is current_step."""
    summary = _summary()
    payload = {"cluster": "sre-test-cluster", "namespace": "test-incidents", "pod": "imagepull-pod",
               "severity": "high"}
    obs_event = {"run_id": "run_test", "cluster": "sre-test-cluster", "namespace": "test-incidents",
                 "pod": "imagepull-pod", "tools_called": 2, "evidence_count": 1, "latency_ms": 1000,
                 "tokens_total": 100, "estimated_cost_usd": 0.001}
    report = _build_rca_report(
        payload, summary, {"confidence_band": "review", "current_step": 3}, {}, obs_event, ["ev_003"],
    )
    assert "Loops: 3" in report


def test_impact_section_never_asserts_degraded_when_outcome_is_insufficient_evidence():
    """Regression for issue #61: the report used to say 'Service Status: DEGRADED' and
    'Dependent services may be affected' unconditionally, even when the investigation
    never reached a real conclusion. Must say plainly it wasn't determined instead."""
    summary = _summary(outcome="insufficient_evidence")
    payload = {"cluster": "sre-test-cluster", "namespace": "test-incidents", "pod": "imagepull-pod",
               "severity": "high"}
    obs_event = {"run_id": "run_test", "cluster": "sre-test-cluster", "namespace": "test-incidents",
                 "pod": "imagepull-pod", "tools_called": 0, "evidence_count": 0, "latency_ms": 500,
                 "tokens_total": 50, "estimated_cost_usd": 0.0001}
    report = _build_rca_report(payload, summary, {"confidence_band": "escalate"}, {}, obs_event, [])
    assert "DEGRADED" not in report
    assert "Dependent services may be affected" not in report
    assert "Not determined" in report


def test_impact_section_still_does_not_overclaim_even_with_a_confirmed_outcome():
    """A confirmed outcome with real evidence still must NOT assert a specific service-status
    claim we never actually checked (that's issue #95's real fix, not this quick one) --
    honest 'not independently checked' language, not a guessed DEGRADED/healthy verdict."""
    summary = _summary(outcome="confirmed")
    payload = {"cluster": "sre-test-cluster", "namespace": "test-incidents", "pod": "imagepull-pod",
               "severity": "high"}
    obs_event = {"run_id": "run_test", "cluster": "sre-test-cluster", "namespace": "test-incidents",
                 "pod": "imagepull-pod", "tools_called": 2, "evidence_count": 1, "latency_ms": 1000,
                 "tokens_total": 100, "estimated_cost_usd": 0.001}
    report = _build_rca_report(payload, summary, {"confidence_band": "auto"}, {}, obs_event, ["ev_003"])
    assert "DEGRADED" not in report
    assert "Dependent services may be affected" not in report
    assert "Not independently checked" in report


def test_rca_report_labels_observed_facts_and_reasoning_separately():
    """issue #206 follow-up: a human reading the plain-text report must be able to tell
    which statements were directly observed vs. reasoned, without opening the raw JSON."""
    summary = _summary(claims=[
        {"text": "Pod imagepull-pod is in ImagePullBackOff.", "claim_type": "observed_fact",
         "grounding_status": "grounded", "support_strength": 1.0},
        {"text": "This is caused by the scheduler repeatedly failing to pull the image.",
         "claim_type": "supported_inference", "grounding_status": "grounded", "support_strength": 1.0},
        {"text": "Some other guess with weak backing.", "claim_type": "observed_fact",
         "grounding_status": "weak_overlap", "support_strength": 0.4},
        {"text": "Recommend editing the pod spec.", "claim_type": "recommendation",
         "grounding_status": "grounded", "support_strength": 1.0},
    ])
    payload = {"cluster": "sre-test-cluster", "namespace": "test-incidents", "pod": "imagepull-pod",
               "severity": "high"}
    obs_event = {"run_id": "run_test", "cluster": "sre-test-cluster", "namespace": "test-incidents",
                 "pod": "imagepull-pod", "tools_called": 2, "evidence_count": 1, "latency_ms": 1000,
                 "tokens_total": 100, "estimated_cost_usd": 0.001}
    report = _build_rca_report(payload, summary, {"confidence_band": "review"}, {}, obs_event, ["ev_003"])

    assert "Claim Breakdown" in report
    assert "[OBSERVED , verified    ] Pod imagepull-pod is in ImagePullBackOff." in report
    assert "[REASONING, verified    ] This is caused by the scheduler" in report
    assert "[OBSERVED , weak support] Some other guess with weak backing." in report
    # The recommendation claim is not part of the root-cause reasoning trail -- must not
    # appear in the breakdown at all, same filter score_root_cause_confidence itself uses.
    assert "Recommend editing the pod spec" not in report
