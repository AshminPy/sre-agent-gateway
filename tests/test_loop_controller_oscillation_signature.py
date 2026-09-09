"""Regression tests for _is_oscillating()'s call-signature fix.

Bug: _is_oscillating() compared only tool NAME across the last 4 successful calls.
describe_pod(pod=A) -> get_logs(pod=A) -> describe_pod(pod=B) -> get_logs(pod=B)
matched the name-only A-B-A-B pattern (describe/logs/describe/logs) and was wrongly
flagged as oscillation, even though the agent was legitimately investigating two
different pods, not looping on one target. Fixed by including normalized args in the
comparison signature (agent/nodes/loop_controller.py's _tool_signature()).
"""
from agent.nodes.loop_controller import _is_oscillating, loop_controller


def _call(tool: str, args: dict, ok: bool = True) -> dict:
    return {"tool": tool, "args": args, "ok": ok}


def _state_with_history(history: list) -> dict:
    return {
        "run_id": "run_test",
        "investigation": {
            "current_step": 3, "max_steps": 10, "min_steps": 2,
            "enough_evidence": False, "tokens_total": 0, "started_at": 0,
        },
        "tool_history": history, "evidence_ids": [], "evidence_store": {},
        "current_action": {},
    }


def test_different_pods_alternating_same_tool_pair_is_not_oscillation():
    """describe(A) -> logs(A) -> describe(B) -> logs(B): a legitimate two-pod
    investigation, must NOT be flagged as oscillating."""
    history = [
        _call("describe_pod_detail", {"pod_name": "pod-a"}),
        _call("get_current_logs", {"pod_name": "pod-a"}),
        _call("describe_pod_detail", {"pod_name": "pod-b"}),
        _call("get_current_logs", {"pod_name": "pod-b"}),
    ]
    assert _is_oscillating(_state_with_history(history)) is False

    result = loop_controller(_state_with_history(history))
    assert result["investigation"]["loop_exit_reason"] != "oscillation_detected"


def test_same_pod_genuinely_alternating_identical_calls_is_oscillation():
    """describe(A) -> logs(A) -> describe(A) -> logs(A): same target, no new
    information each round -- this IS real no-progress oscillation and must still be
    detected."""
    history = [
        _call("describe_pod_detail", {"pod_name": "pod-a"}),
        _call("get_current_logs", {"pod_name": "pod-a"}),
        _call("describe_pod_detail", {"pod_name": "pod-a"}),
        _call("get_current_logs", {"pod_name": "pod-a"}),
    ]
    assert _is_oscillating(_state_with_history(history)) is True

    result = loop_controller(_state_with_history(history))
    assert result["investigation"]["loop_exit_reason"] == "oscillation_detected"


def test_three_different_targets_in_sequence_is_not_oscillation():
    """describe(A) -> logs(A) -> describe(A) -> logs(B): the last pair differs from
    the first pair's target on one side -- not a clean A-B-A-B repeat, must not
    trigger."""
    history = [
        _call("describe_pod_detail", {"pod_name": "pod-a"}),
        _call("get_current_logs", {"pod_name": "pod-a"}),
        _call("describe_pod_detail", {"pod_name": "pod-a"}),
        _call("get_current_logs", {"pod_name": "pod-b"}),
    ]
    assert _is_oscillating(_state_with_history(history)) is False


def test_failed_calls_are_excluded_from_the_signature_history():
    """Only successful (ok=True) calls count toward the pattern -- a failed call must
    not corrupt the signature window."""
    history = [
        _call("describe_pod_detail", {"pod_name": "pod-a"}),
        _call("get_current_logs", {"pod_name": "pod-a"}, ok=False),  # excluded
        _call("get_current_logs", {"pod_name": "pod-a"}),
        _call("describe_pod_detail", {"pod_name": "pod-a"}),
        _call("get_current_logs", {"pod_name": "pod-a"}),
    ]
    # ok_history (excluding the failure) = [describe(a), logs(a), describe(a), logs(a)]
    # -- a genuine repeat, must still be caught.
    assert _is_oscillating(_state_with_history(history)) is True


def test_fewer_than_four_successful_calls_never_triggers():
    history = [
        _call("describe_pod_detail", {"pod_name": "pod-a"}),
        _call("get_current_logs", {"pod_name": "pod-a"}),
        _call("describe_pod_detail", {"pod_name": "pod-a"}),
    ]
    assert _is_oscillating(_state_with_history(history)) is False


def test_args_with_different_value_types_still_normalize_to_same_signature():
    """A pod_name passed as int vs str (unlikely in practice, but args come from
    LLM-produced JSON) must not defeat a genuine repeat-detection via a spurious type
    mismatch."""
    history = [
        _call("get_current_logs", {"replica": 1}),
        _call("describe_pod_detail", {"replica": 1}),
        _call("get_current_logs", {"replica": "1"}),
        _call("describe_pod_detail", {"replica": "1"}),
    ]
    assert _is_oscillating(_state_with_history(history)) is True
