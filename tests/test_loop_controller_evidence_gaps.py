"""Regression tests for issue #69: the loop must not exit on enough_evidence=True when
task_evaluator's deterministic completeness check found a required evidence domain still
missing and iteration budget remains.

Real-world proof (see docs/testing/e2e-honest-baseline-2026-08-09-notification-relay.md):
the agent's own evidence_gaps field named a specific unresolved gap, yet the loop still
exited with loop_exit_reason=confidence_sufficient -- because enough_evidence (the LLM's
own self-report) was honored unconditionally, never checked against
completeness["missing_required_domains"] computed in the same call.
"""
from agent.nodes.loop_controller import loop_controller


def _state(enough_evidence: bool, missing_required_domains: list, current_step: int = 3,
           max_steps: int = 5) -> dict:
    return {
        "run_id": "run_test",
        "investigation": {
            "current_step": current_step, "max_steps": max_steps, "min_steps": 2,
            "enough_evidence": enough_evidence, "tokens_total": 0, "started_at": 0,
            "completeness": {"missing_required_domains": missing_required_domains},
        },
        "tool_history": [], "evidence_ids": ["ev_001"], "evidence_store": {"ev_001": {}},
        "current_action": {},
    }


def test_enough_evidence_with_missing_domain_and_budget_left_forces_continue():
    result = loop_controller(_state(enough_evidence=True,
                                      missing_required_domains=["kubernetes_events"],
                                      current_step=2, max_steps=5))
    assert result["investigation"]["loop_exit_reason"] is None
    assert result["investigation"]["status"] == "running"


def test_enough_evidence_with_no_missing_domains_still_exits_immediately():
    result = loop_controller(_state(enough_evidence=True, missing_required_domains=[],
                                      current_step=2, max_steps=5))
    assert result["investigation"]["loop_exit_reason"] == "confidence_sufficient"
    assert result["investigation"]["status"] == "done"


def test_enough_evidence_with_missing_domain_but_no_budget_left_still_exits():
    # step == max_steps - 1 going into loop_controller means step+1 == max_steps -- no
    # budget remains for one more iteration, so the override must not apply.
    result = loop_controller(_state(enough_evidence=True,
                                      missing_required_domains=["kubernetes_events"],
                                      current_step=4, max_steps=5))
    assert result["investigation"]["loop_exit_reason"] == "confidence_sufficient"


def test_not_enough_evidence_is_unaffected_by_missing_domains_check():
    result = loop_controller(_state(enough_evidence=False,
                                      missing_required_domains=["kubernetes_events"],
                                      current_step=2, max_steps=5))
    assert result["investigation"]["loop_exit_reason"] is None
