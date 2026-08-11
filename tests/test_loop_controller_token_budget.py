"""Regression tests for the token-budget hard cap (issue #63).

MAX_TOKENS_PER_RUN already existed and was already enforced by
agent/nodes/loop_controller.py before this change -- what changed is that
Terraform now sets it (iac/agent/variables.tf's max_tokens_per_run ->
agent_engine.tf's MAX_TOKENS_PER_RUN), instead of production silently running
on the Python-side default forever. These tests prove the env var reading and
exit-decision logic that number drives, unchanged by this PR, still works --
and specifically that there is no separate, second, hardcoded threshold
anywhere in this module competing with the Terraform-sourced one.

_MAX_TOKENS is read from os.environ at module import time, so tests reload
the module under controlled env vars rather than monkeypatching the
already-imported constant directly (same pattern as
tests/test_llm_gemini_adapter.py's pricing tests).
"""
import importlib

import agent.nodes.loop_controller as loop_controller_mod


def _reload_with_env(monkeypatch, value: str | None):
    monkeypatch.delenv("MAX_TOKENS_PER_RUN", raising=False)
    if value is not None:
        monkeypatch.setenv("MAX_TOKENS_PER_RUN", value)
    importlib.reload(loop_controller_mod)
    return loop_controller_mod


def _state(tokens_total: int, **overrides) -> dict:
    base = {
        "run_id": "run_test",
        "investigation": {
            "current_step": 1, "max_steps": 5, "min_steps": 2,
            "enough_evidence": False, "tokens_total": tokens_total,
            "started_at": 0,
        },
        "tool_history": [], "evidence_ids": [], "evidence_store": {},
        "current_action": {},
    }
    base["investigation"].update(overrides)
    return base


def test_missing_env_var_defaults_to_100000_matching_terraform_default(monkeypatch):
    """iac/agent/variables.tf's max_tokens_per_run defaults to 100000 specifically to
    match this fallback -- if this default ever changes without the Terraform default
    changing to match, production behavior would silently diverge from what Terraform
    claims it is."""
    mod = _reload_with_env(monkeypatch, None)
    assert mod._MAX_TOKENS == 100000


def test_env_var_value_is_honored_when_terraform_sets_it(monkeypatch):
    mod = _reload_with_env(monkeypatch, "50000")
    assert mod._MAX_TOKENS == 50000


def test_invalid_env_value_falls_back_to_100000_not_a_crash(monkeypatch):
    mod = _reload_with_env(monkeypatch, "not-a-number")
    assert mod._MAX_TOKENS == 100000


def test_zero_disables_the_hard_cap(monkeypatch):
    mod = _reload_with_env(monkeypatch, "0")
    assert mod._token_budget_exceeded(_state(tokens_total=10_000_000)) is False


def test_under_budget_does_not_trigger_exit(monkeypatch):
    mod = _reload_with_env(monkeypatch, "50000")
    assert mod._token_budget_exceeded(_state(tokens_total=49_999)) is False


def test_over_budget_triggers_exit_with_the_real_reason(monkeypatch):
    mod = _reload_with_env(monkeypatch, "50000")
    assert mod._token_budget_exceeded(_state(tokens_total=50_001)) is True

    result = mod.loop_controller(_state(tokens_total=50_001))
    assert result["investigation"]["loop_exit_reason"] == "token_budget_exceeded"
    assert result["investigation"]["status"] == "done"


def teardown_module(module):
    import os
    os.environ.pop("MAX_TOKENS_PER_RUN", None)
    importlib.reload(loop_controller_mod)
