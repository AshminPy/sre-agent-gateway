"""Section 10 correction (2026-09-09): _derive_status() used to return "error" whenever
state["errors"] was non-empty at all -- but tool_executor.py appends one entry per
individual tool-call failure, including a single transient blip the agent successfully
worked around later in the same run. Live-discovered via the Phase 1 50-case campaign:
13 of 14 runs the old logic flagged "error" had root-cause confidence 0.54-0.95 and
completeness 0.79-0.97 -- genuinely correct investigations, not failures. These tests
pin the corrected behavior: only a genuine upstream failure, an abnormal loop exit, or
a real evidence-storage write failure counts as "error".
"""
from agent.nodes.rca_builder import _derive_status


def _state(inv_status="done", loop_exit_reason=None, errors=None):
    return {
        "investigation": {"status": inv_status, "loop_exit_reason": loop_exit_reason},
        "errors": errors or [],
    }


def test_single_transient_tool_failure_that_was_recovered_is_success():
    """The exact live-observed pattern: one tool call failed, the run still completed
    normally (loop_exit_reason=None, investigation.status="done"), high confidence."""
    state = _state(errors=["tool=list_k8s_events error=MCP_TOOL_ERROR: unavailable"])
    assert _derive_status(state) == "success"


def test_clean_run_with_no_errors_is_success():
    state = _state()
    assert _derive_status(state) == "success"


def test_upstream_failed_status_is_error():
    """context_resolver/mcp_router/input_normalizer's genuine fatal-failure signal --
    the same one loop_controller.py's upstream_failed check and
    scripts/run_50case_campaign.py's grade() already treat as authoritative."""
    state = _state(inv_status="failed")
    assert _derive_status(state) == "error"


def test_abnormal_loop_exit_is_error():
    state = _state(loop_exit_reason="consecutive_tool_failures",
                    errors=["tool=list_k8s_events error=unavailable",
                            "tool=describe_k8s_resource error=unavailable",
                            "loop exited early: consecutive_tool_failures at step 2"])
    assert _derive_status(state) == "error"


def test_other_abnormal_exit_reasons_are_error():
    for reason in ("stuck_detected", "zero_new_facts", "oscillation_detected",
                   "timeout", "token_budget_exceeded", "safety_budget_exceeded"):
        assert _derive_status(_state(loop_exit_reason=reason)) == "error", reason


def test_evidence_storage_failure_is_error():
    state = _state()
    assert _derive_status(state, evidence_storage_failed_count=1) == "error"


def test_normal_completion_with_zero_evidence_storage_failures_is_success():
    state = _state()
    assert _derive_status(state, evidence_storage_failed_count=0) == "success"
