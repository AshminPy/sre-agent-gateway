"""Regression test: run_remote() must unwrap the SAME level run_local() does.

Fourth review pass, 2026-08-27 -- the eval harness, which the first three passes
did not cover.

Gap 16 (found here): run_remote() wrapped the FULL top-level SREAgent.query()
response dict as {"final_summary": <that whole dict>}. score_case() then read
tools_called/likely_root_cause/confidence_score off that outer dict, but those
three keys only exist one level down, inside result["summary"] -- confirmed by
reading agent/main.py's _finalize_query() (returns `result` unchanged, no
reshaping) and _finalize_investigation_result() (which builds "summary" as a
nested sub-dict, not top-level).

Reproduced directly before the fix: a PERFECT agent response --
tools_called=["get_k8s_resource","list_k8s_events"], the exact expected root
cause text, confidence=0.8 -- scored predicted_tools=[], root_cause='',
confidence=0.0, PASSED=False. Remote-mode eval failed every single golden case
regardless of real agent quality, silently, with zero indication the harness
itself -- not the agent -- was broken. This is exactly the failure class the
whole review was for: something not-real (a malformed score) presented as
real (a failed agent).

local mode (run_local) was checked and does NOT have this bug: rca_builder.py
sets state["final_summary"] = result directly (the inner dict itself, not a
wrapper), so final_state.get("final_summary", {}) is already the correct shape.
"""
from unittest.mock import MagicMock, patch

import pytest

from agent.eval.run_eval import run_remote, score_case

CASE = {
    "id": "imagepull-001",
    "payload": {"user_query": "x", "resource_hints": {}, "incident": {}},
    "expected_trajectory": ["get_k8s_resource", "list_k8s_events"],
    "expected_keywords": ["ImagePullBackOff", "ErrImagePull", "image"],
    "expected_confidence_min": 0.65,
}

# The real shape SREAgent.query() -> _finalize_query() returns: a top-level dict
# with status/confidence/outcome/run_id AND a nested "summary" sub-dict that
# holds tools_called/likely_root_cause/confidence_score.
REAL_AGENT_ENGINE_RESPONSE = {
    "status": "done",
    "confidence": 0.8,
    "confidence_band": "auto",
    "outcome": "confirmed",
    "run_id": "run_x",
    "summary": {
        "tools_called": ["get_k8s_resource", "list_k8s_events"],
        "likely_root_cause": "Pod imagepull-pod in ImagePullBackOff: ErrImagePull, image not found",
        "confidence_score": 0.8,
        "confidence_band": "auto",
        "outcome": "confirmed",
    },
}


def _run_remote_against(response_dict: dict, case: dict = CASE) -> dict:
    """Drives the REAL run_remote(), only the network client and protobuf
    conversion are mocked -- proves the fix through the actual function, not a
    hand-simulation of what it should do."""
    with patch("google.cloud.aiplatform_v1beta1.ReasoningEngineExecutionServiceClient") as MockClient, \
         patch("google.protobuf.json_format.MessageToDict", return_value={"output": response_dict}):
        MockClient.return_value.query_reasoning_engine.return_value = MagicMock()
        return run_remote(case, engine_id="fake-engine")


def test_run_remote_unwraps_to_the_inner_summary_dict():
    result = _run_remote_against(REAL_AGENT_ENGINE_RESPONSE)
    inner = result["final_summary"]
    assert inner["tools_called"] == ["get_k8s_resource", "list_k8s_events"]
    assert "ImagePullBackOff" in inner["likely_root_cause"]
    assert inner["confidence_score"] == 0.8


def test_a_perfect_agent_response_now_scores_as_passed():
    """The exact regression: this used to score predicted_tools=[], confidence=0.0."""
    result = _run_remote_against(REAL_AGENT_ENGINE_RESPONSE)
    score = score_case(result, CASE)
    assert score["predicted_tools"] == ["get_k8s_resource", "list_k8s_events"]
    assert score["confidence"] == 0.8
    assert score["trajectory_recall"] == 1.0
    assert score["passed"] is True


def test_double_wrapped_shape_would_have_failed_every_metric():
    """Documents the OLD bug precisely, so it cannot silently come back. This is
    what {"final_summary": result} (the full outer dict, not result["summary"])
    produces when scored -- run_remote() itself no longer does this."""
    double_wrapped = {"final_summary": REAL_AGENT_ENGINE_RESPONSE}
    score = score_case(double_wrapped, CASE)
    assert score["predicted_tools"] == []
    assert score["root_cause"] == ""
    assert score["confidence"] == 0.0
    assert score["passed"] is False


def test_a_response_with_no_summary_key_logs_and_degrades_safely(caplog):
    """Malformed/unexpected response shape must not crash, and must not silently
    fake a pass -- it should score as a real failure with a clear log line."""
    import logging
    malformed = {"status": "failed", "error": "boom"}
    with caplog.at_level(logging.ERROR):
        result = _run_remote_against(malformed)
    assert result["final_summary"] == {"_latency_seconds": result["final_summary"]["_latency_seconds"]}
    assert "no 'summary' dict" in caplog.text
    score = score_case(result, CASE)
    assert score["passed"] is False


def test_local_mode_shape_is_unaffected():
    """run_local() was checked separately and never had this bug -- guard that
    the eval scorer still handles its (already-correct) shape."""
    already_correct = {"final_summary": REAL_AGENT_ENGINE_RESPONSE["summary"]}
    score = score_case(already_correct, CASE)
    assert score["passed"] is True
