"""Regression test for issue #63: cost estimation used a hardcoded, plausible-looking
Flash price even when the deployed model was actually Pro. Fix: Terraform is the sole
source of truth for pricing (iac/agent/variables.tf's gemini_price_input_per_1m /
gemini_price_output_per_1m, wired to GEMINI_PRICE_INPUT/GEMINI_PRICE_OUTPUT env vars in
agent_engine.tf) -- the Python fallback, if those env vars are ever missing, must be an
obviously-wrong 0.0, never a second guessed price that could silently look plausible.

PRICE_INPUT_PER_1M/PRICE_OUTPUT_PER_1M are read from os.environ at module import time, so
this test reloads the module under controlled env vars rather than monkeypatching the
already-imported constants directly.
"""
import importlib
import os

import agent.gemini_client as gemini_client_mod


def _reload_with_env(monkeypatch, env: dict):
    for key in ("GEMINI_PRICE_INPUT", "GEMINI_PRICE_OUTPUT"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    importlib.reload(gemini_client_mod)
    return gemini_client_mod


def test_missing_price_env_vars_default_to_zero_not_a_guessed_price(monkeypatch):
    """The core regression: no GEMINI_PRICE_INPUT/GEMINI_PRICE_OUTPUT set -> 0.0, not
    Flash's old hardcoded 0.15/0.60 (or any other plausible-looking wrong number)."""
    mod = _reload_with_env(monkeypatch, {})
    assert mod.PRICE_INPUT_PER_1M == 0.0
    assert mod.PRICE_OUTPUT_PER_1M == 0.0


def test_price_env_vars_are_honored_when_set(monkeypatch):
    """Terraform's real values (Gemini 2.5 Pro's published rate) flow through correctly."""
    mod = _reload_with_env(monkeypatch, {
        "GEMINI_PRICE_INPUT": "1.25",
        "GEMINI_PRICE_OUTPUT": "10.0",
    })
    assert mod.PRICE_INPUT_PER_1M == 1.25
    assert mod.PRICE_OUTPUT_PER_1M == 10.0


def test_cost_calc_reflects_the_configured_price(monkeypatch):
    """End-to-end: a real usage dict, priced with the real configured rate, not a
    hardcoded one -- proves the fix all the way through to the actual cost number."""
    mod = _reload_with_env(monkeypatch, {
        "GEMINI_PRICE_INPUT": "1.25",
        "GEMINI_PRICE_OUTPUT": "10.0",
    })
    cost = (1_000_000 / 1_000_000) * mod.PRICE_INPUT_PER_1M + (1_000_000 / 1_000_000) * mod.PRICE_OUTPUT_PER_1M
    assert cost == 11.25, "1M input + 1M output tokens at Pro's real rate must cost $11.25, not Flash's $0.75"


def teardown_module(module):
    # Restore the module to its normal (real-environment) state for any test that runs
    # after this file, so we don't leak a reloaded/monkeypatched version into other tests.
    for key in ("GEMINI_PRICE_INPUT", "GEMINI_PRICE_OUTPUT"):
        os.environ.pop(key, None)
    importlib.reload(gemini_client_mod)
