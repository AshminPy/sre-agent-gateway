"""Regression tests for the investigation safety budget (issue #103, Slice 1).

This is admission control, not interruption: it stops loop_controller from
starting another expensive round (task_planner -> mcp_router -> tool_executor
-> evidence_extractor -> task_evaluator, each making its own Gemini call) once
elapsed time leaves too little headroom before the MANAGED Vertex AI Agent
Engine request/stream boundary observed at ~300-300.6s (issue #103). It is
deliberately separate from max_duration_seconds (agent/state.py, 540s,
unchanged) -- that stays the existing, already-documented (iac/agent/
monitoring.tf) hard cap, unrelated to this smaller, earlier check.

It cannot interrupt a single call already in progress -- a call that starts
just under the budget and itself runs long (observed up to ~69s during
issue #103's investigation) can still push the total past the managed
boundary. That gap is out of scope for this slice.

_SAFETY_BUDGET_SECONDS is read from os.environ at module import time, so
tests reload the module under controlled env vars rather than monkeypatching
the already-imported constant directly (same pattern as
tests/test_loop_controller_token_budget.py).
"""
import importlib
import time

import agent.nodes.loop_controller as loop_controller_mod


def _reload_with_env(monkeypatch, value: str | None):
    monkeypatch.delenv("SAFETY_BUDGET_SECONDS", raising=False)
    if value is not None:
        monkeypatch.setenv("SAFETY_BUDGET_SECONDS", value)
    importlib.reload(loop_controller_mod)
    return loop_controller_mod


def _state(elapsed_seconds: float, **overrides) -> dict:
    base = {
        "run_id": "run_test",
        "investigation": {
            "current_step": 1, "max_steps": 5, "min_steps": 2,
            "enough_evidence": False, "tokens_total": 0,
            "started_at": time.time() - elapsed_seconds,
        },
        "tool_history": [], "evidence_ids": [], "evidence_store": {},
        "current_action": {},
    }
    base["investigation"].update(overrides)
    return base


def test_missing_env_var_defaults_to_200(monkeypatch):
    mod = _reload_with_env(monkeypatch, None)
    assert mod._SAFETY_BUDGET_SECONDS == 200


def test_env_var_value_is_honored(monkeypatch):
    mod = _reload_with_env(monkeypatch, "180")
    assert mod._SAFETY_BUDGET_SECONDS == 180


def test_invalid_env_value_falls_back_to_200_not_a_crash(monkeypatch):
    mod = _reload_with_env(monkeypatch, "not-a-number")
    assert mod._SAFETY_BUDGET_SECONDS == 200


def test_zero_or_negative_falls_back_to_200(monkeypatch):
    mod = _reload_with_env(monkeypatch, "0")
    assert mod._SAFETY_BUDGET_SECONDS == 200

    mod = _reload_with_env(monkeypatch, "-50")
    assert mod._SAFETY_BUDGET_SECONDS == 200


def test_value_at_or_below_200_is_accepted(monkeypatch):
    mod = _reload_with_env(monkeypatch, "180")
    assert mod._SAFETY_BUDGET_SECONDS == 180

    mod = _reload_with_env(monkeypatch, "200")
    assert mod._SAFETY_BUDGET_SECONDS == 200


def test_value_above_200_is_capped_to_200(monkeypatch):
    """An env override may only LOWER the budget, never raise it above the
    default -- a misconfigured value above 200 must not silently widen the
    margin under the managed ~300s stream boundary away."""
    mod = _reload_with_env(monkeypatch, "201")
    assert mod._SAFETY_BUDGET_SECONDS == 200

    mod = _reload_with_env(monkeypatch, "260")
    assert mod._SAFETY_BUDGET_SECONDS == 200


def test_under_budget_does_not_trigger_exit(monkeypatch):
    mod = _reload_with_env(monkeypatch, "200")
    assert mod._is_safety_budget_exceeded(_state(elapsed_seconds=150)) is False

    result = mod.loop_controller(_state(elapsed_seconds=150))
    assert result["investigation"]["loop_exit_reason"] != "safety_budget_exceeded"


def test_over_budget_triggers_exit_with_the_real_reason(monkeypatch):
    mod = _reload_with_env(monkeypatch, "200")
    assert mod._is_safety_budget_exceeded(_state(elapsed_seconds=201)) is True

    result = mod.loop_controller(_state(elapsed_seconds=201))
    assert result["investigation"]["loop_exit_reason"] == "safety_budget_exceeded"
    assert result["investigation"]["status"] == "done"
    assert "errors" in result
    assert "safety_budget_exceeded" in result["errors"][0]


def test_enough_evidence_wins_even_past_safety_budget(monkeypatch):
    """enough_evidence=True must always exit as confidence_sufficient,
    even when elapsed time is also past the safety budget."""
    mod = _reload_with_env(monkeypatch, "200")
    state = _state(elapsed_seconds=250, enough_evidence=True)

    result = mod.loop_controller(state)
    assert result["investigation"]["loop_exit_reason"] == "confidence_sufficient"


def test_safety_budget_fires_before_the_existing_540s_timeout(monkeypatch):
    """At an elapsed time past the safety budget but still well under the
    existing 540s max_duration_seconds hard cap, the new, earlier,
    smaller check must be the one that fires -- not the old one."""
    mod = _reload_with_env(monkeypatch, "200")
    state = _state(elapsed_seconds=220, max_duration_seconds=540)

    assert mod._is_timed_out(state) is False
    assert mod._is_safety_budget_exceeded(state) is True

    result = mod.loop_controller(state)
    assert result["investigation"]["loop_exit_reason"] == "safety_budget_exceeded"


def teardown_module(module):
    import os
    os.environ.pop("SAFETY_BUDGET_SECONDS", None)
    importlib.reload(loop_controller_mod)
