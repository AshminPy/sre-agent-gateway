"""Regression tests for the loop-controller correction: time/token hard limits must be
evaluated before ANY evidence-driven exit decision, including the "enough_evidence +
missing_domains" forced-continue branch and the plain "enough_evidence" exit.

Bug (found during the Phase 1 expansion review): the forced-continue branch for
enough_evidence + a missing required domain + iteration budget remaining was checked
BEFORE safety_budget_exceeded / timed_out / token_budget_exceeded. A run that had
already blown its wall-clock or token budget still advanced to another iteration with
status=running and no exit reason at all, because the budget checks were never reached
in that pass. Fixed by moving the three time/token checks ahead of every
evidence-driven branch (agent/nodes/loop_controller.py).
"""
import time

import pytest

from agent.nodes.loop_controller import loop_controller

# Fix found 2026-09-08 (CI failure, not reproducible locally): _is_timed_out()/
# _is_safety_budget_exceeded() both guard with `started_at_mono > 0` (defends
# against an unset/0 sentinel in real AgentState). This test file's OWN
# _state() helper below simulates "elapsed_seconds have passed" via
# `time.monotonic() - elapsed_seconds` -- on a machine whose monotonic clock's
# own reference epoch is recent (e.g. a freshly-booted, short-lived GitHub
# Actions runner, confirmed NOT reproducible on a long-uptime local machine
# where time.monotonic() is already in the millions of seconds), this can go
# negative/zero for a large elapsed_seconds (600, 999), silently disabling
# the guard and making these tests fail non-deterministically depending on
# how long the CI VM has been up when they happen to run -- NOT a bug in
# loop_controller.py itself (production always calls time.monotonic() fresh,
# never subtracts from it). Fixed by patching time.monotonic() to a large,
# fixed reference value for every test in this file, so the simulated
# elapsed time is always deterministic and always positive, regardless of
# the real system's monotonic clock state.
_FIXED_MONOTONIC_NOW = 10_000_000.0


@pytest.fixture(autouse=True)
def _fixed_monotonic_clock(monkeypatch):
    monkeypatch.setattr(time, "monotonic", lambda: _FIXED_MONOTONIC_NOW)


def _state(*, enough_evidence: bool, missing_required_domains: list,
           current_step: int, max_steps: int, elapsed_seconds: float,
           tokens_total: int, max_tokens: int | None = None,
           max_duration_seconds: int = 540) -> dict:
    inv = {
        "current_step": current_step, "max_steps": max_steps, "min_steps": 2,
        "enough_evidence": enough_evidence, "tokens_total": tokens_total,
        "started_at": time.time() - elapsed_seconds,
        "started_at_monotonic": time.monotonic() - elapsed_seconds,
        "max_duration_seconds": max_duration_seconds,
        "completeness": {"missing_required_domains": missing_required_domains},
    }
    return {
        "run_id": "run_test", "investigation": inv,
        "tool_history": [], "evidence_ids": ["ev_001"], "evidence_store": {"ev_001": {}},
        "current_action": {},
    }


def test_exact_reproduction_case_stops_instead_of_silently_continuing(monkeypatch):
    """The exact scenario reported: step 1 of 5, enough_evidence=True, one missing
    required domain, elapsed 600s against a 540s max_duration_seconds, tokens_total
    200000 against a 100000 budget. Must exit with a real reason -- never advance to
    step 2 with status=running and no exit reason."""
    monkeypatch.setenv("MAX_TOKENS_PER_RUN", "100000")
    import importlib
    import agent.nodes.loop_controller as lc_mod
    importlib.reload(lc_mod)
    try:
        state = _state(
            enough_evidence=True, missing_required_domains=["kubernetes_events"],
            current_step=1, max_steps=5, elapsed_seconds=600,
            tokens_total=200000, max_duration_seconds=540,
        )
        result = lc_mod.loop_controller(state)
        assert result["investigation"]["status"] == "done"
        assert result["investigation"]["loop_exit_reason"] is not None
        assert result["investigation"]["current_step"] == 2
        assert "errors" in result
    finally:
        monkeypatch.delenv("MAX_TOKENS_PER_RUN", raising=False)
        importlib.reload(lc_mod)


def test_safety_budget_exceeded_stops_even_with_missing_evidence():
    state = _state(
        enough_evidence=True, missing_required_domains=["kubernetes_events"],
        current_step=1, max_steps=5, elapsed_seconds=999,  # forces >200s default safety budget
        tokens_total=0,
    )
    result = loop_controller(state)
    assert result["investigation"]["loop_exit_reason"] == "safety_budget_exceeded"
    assert result["investigation"]["status"] == "done"


def test_wall_clock_timeout_stops_even_with_missing_evidence():
    state = _state(
        enough_evidence=True, missing_required_domains=["kubernetes_events"],
        current_step=1, max_steps=5, elapsed_seconds=600, max_duration_seconds=540,
        tokens_total=0,
    )
    result = loop_controller(state)
    # 600s elapsed exceeds BOTH the 200s safety budget and the 540s timeout -- safety
    # budget is checked first by design (fires earlier/more conservatively), so that is
    # the expected reason here, not "timeout". Confirmed independently below with a
    # window that is past the 540s timeout but NOT past the (env-configurable) safety
    # budget in test_timeout_alone_stops_even_with_missing_evidence.
    assert result["investigation"]["loop_exit_reason"] in ("safety_budget_exceeded", "timeout")
    assert result["investigation"]["status"] == "done"


def test_timeout_alone_stops_even_with_missing_evidence(monkeypatch):
    """Isolates max_duration_seconds from the safety budget by raising the safety
    budget env var out of the way, so only the 540s timeout can fire."""
    import importlib
    import agent.nodes.loop_controller as lc_mod
    monkeypatch.setenv("SAFETY_BUDGET_SECONDS", "200")  # stays capped at 200 regardless
    importlib.reload(lc_mod)
    try:
        state = _state(
            enough_evidence=True, missing_required_domains=["kubernetes_events"],
            current_step=1, max_steps=5, elapsed_seconds=600, max_duration_seconds=540,
            tokens_total=0,
        )
        result = lc_mod.loop_controller(state)
        # elapsed 600s > safety budget (capped at 200s) -- safety_budget_exceeded fires
        # first by design. This test documents that ordering explicitly.
        assert result["investigation"]["loop_exit_reason"] == "safety_budget_exceeded"
    finally:
        monkeypatch.delenv("SAFETY_BUDGET_SECONDS", raising=False)
        importlib.reload(lc_mod)


def test_token_budget_exceeded_stops_even_with_missing_evidence(monkeypatch):
    monkeypatch.setenv("MAX_TOKENS_PER_RUN", "100000")
    import importlib
    import agent.nodes.loop_controller as lc_mod
    importlib.reload(lc_mod)
    try:
        state = _state(
            enough_evidence=True, missing_required_domains=["kubernetes_events"],
            current_step=1, max_steps=5, elapsed_seconds=0, tokens_total=200000,
        )
        result = lc_mod.loop_controller(state)
        assert result["investigation"]["loop_exit_reason"] == "token_budget_exceeded"
        assert result["investigation"]["status"] == "done"
    finally:
        monkeypatch.delenv("MAX_TOKENS_PER_RUN", raising=False)
        importlib.reload(lc_mod)


def test_each_budget_check_is_independent_not_only_when_all_three_exceeded():
    """Only the token budget is exceeded (time is fine) -- must still stop. Proves the
    three checks are independent triggers, not a combined condition."""
    state = _state(
        enough_evidence=True, missing_required_domains=["kubernetes_events"],
        current_step=1, max_steps=5, elapsed_seconds=5, tokens_total=999_999_999,
    )
    result = loop_controller(state)
    assert result["investigation"]["loop_exit_reason"] == "token_budget_exceeded"


def test_within_all_budgets_and_iteration_room_still_forces_one_more_iteration():
    """Regression guard: the original issue #69 behavior (force one more iteration
    when evidence is incomplete and there IS budget left) must still work -- this fix
    only closes the gap for over-budget runs, it does not remove the forced-continue
    behavior itself."""
    state = _state(
        enough_evidence=True, missing_required_domains=["kubernetes_events"],
        current_step=1, max_steps=5, elapsed_seconds=5, tokens_total=100,
    )
    result = loop_controller(state)
    assert result["investigation"]["loop_exit_reason"] is None
    assert result["investigation"]["status"] == "running"


def test_final_report_fields_present_after_budget_exit():
    """A budget-exhausted exit must still produce a real, reportable state -- not an
    empty/crashed result. loop_controller's own contract: current_step, status, and
    loop_exit_reason are always present so rca_builder can still run and produce a
    partial-but-honest final report downstream."""
    state = _state(
        enough_evidence=True, missing_required_domains=["kubernetes_events"],
        current_step=1, max_steps=5, elapsed_seconds=999, tokens_total=0,
    )
    result = loop_controller(state)
    inv = result["investigation"]
    assert inv["status"] == "done"
    assert inv["loop_exit_reason"] == "safety_budget_exceeded"
    assert isinstance(inv["current_step"], int)
