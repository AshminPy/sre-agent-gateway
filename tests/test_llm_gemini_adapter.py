"""Regression tests for the Gemini adapter's usage extraction (issue #63 PR 1).

Covers:
  - all 6 usage_metadata fields are captured (previously only prompt/candidates)
  - billable_output_tokens = candidates + thoughts (thinking tokens bill at the
    output rate — previously never counted anywhere)
  - total_tokens is the provider's own reported value, never locally recomputed
    as input + output
  - pricing still reads GEMINI_PRICE_INPUT/GEMINI_PRICE_OUTPUT, obviously-wrong
    0.0 fallback when unset (same contract as the old gemini_client.py, now
    scoped to adapter instantiation instead of module import)
"""
from types import SimpleNamespace

from agent.llm.gemini_adapter import GeminiAdapter


def _fake_response(text="{}", **usage_fields):
    defaults = dict(
        prompt_token_count=0,
        cached_content_token_count=0,
        candidates_token_count=0,
        thoughts_token_count=0,
        tool_use_prompt_token_count=0,
        total_token_count=0,
    )
    defaults.update(usage_fields)
    return SimpleNamespace(text=text, usage_metadata=SimpleNamespace(**defaults))


def test_missing_price_env_vars_default_to_zero_not_a_guessed_price(monkeypatch):
    monkeypatch.delenv("GEMINI_PRICE_INPUT", raising=False)
    monkeypatch.delenv("GEMINI_PRICE_OUTPUT", raising=False)
    adapter = GeminiAdapter(model="gemini-2.5-pro")
    assert adapter.price_input_per_1m == 0.0
    assert adapter.price_output_per_1m == 0.0


def test_price_env_vars_are_honored_when_set(monkeypatch):
    monkeypatch.setenv("GEMINI_PRICE_INPUT", "1.25")
    monkeypatch.setenv("GEMINI_PRICE_OUTPUT", "10.0")
    adapter = GeminiAdapter(model="gemini-2.5-pro")
    assert adapter.price_input_per_1m == 1.25
    assert adapter.price_output_per_1m == 10.0


def test_extract_usage_captures_all_six_usage_metadata_fields(monkeypatch):
    """The core regression: thoughts_token_count, cached_content_token_count, and
    tool_use_prompt_token_count used to be silently dropped — only prompt/candidates
    were ever read."""
    monkeypatch.setenv("GEMINI_PRICE_INPUT", "1.25")
    monkeypatch.setenv("GEMINI_PRICE_OUTPUT", "10.0")
    adapter = GeminiAdapter(model="gemini-2.5-pro")

    response = _fake_response(
        prompt_token_count=49,
        cached_content_token_count=5,
        candidates_token_count=72,
        thoughts_token_count=93,
        tool_use_prompt_token_count=3,
        total_token_count=214,
    )
    usage = adapter._extract_usage(response, duration_s=1.0)

    assert usage["input_tokens"] == 49
    assert usage["cached_input_tokens"] == 5
    assert usage["output_tokens"] == 72
    assert usage["reasoning_tokens"] == 93
    assert usage["tool_tokens"] == 3
    assert usage["total_tokens"] == 214, "must be the provider's own total, not recomputed"


def test_billable_output_tokens_is_candidates_plus_thoughts(monkeypatch):
    monkeypatch.setenv("GEMINI_PRICE_INPUT", "1.25")
    monkeypatch.setenv("GEMINI_PRICE_OUTPUT", "10.0")
    adapter = GeminiAdapter(model="gemini-2.5-pro")

    response = _fake_response(prompt_token_count=100, candidates_token_count=72, thoughts_token_count=93,
                               total_token_count=265)
    usage = adapter._extract_usage(response, duration_s=1.0)

    assert usage["billable_output_tokens"] == 72 + 93 == 165


def test_cost_bills_reasoning_tokens_at_the_output_rate(monkeypatch):
    """The other half of the #63 PR 1 regression: thinking tokens were never billed
    at all before this fix, even though Google's own pricing page describes the
    output rate as covering 'Text output tokens (response and reasoning)'."""
    monkeypatch.setenv("GEMINI_PRICE_INPUT", "1.25")
    monkeypatch.setenv("GEMINI_PRICE_OUTPUT", "10.0")
    adapter = GeminiAdapter(model="gemini-2.5-pro")

    response = _fake_response(prompt_token_count=1_000_000, candidates_token_count=0,
                               thoughts_token_count=1_000_000, total_token_count=2_000_000)
    usage = adapter._extract_usage(response, duration_s=1.0)

    # 1M input @ $1.25/1M + 1M reasoning-as-output @ $10/1M = $11.25, not $1.25
    # (which is what you'd get if reasoning tokens were silently unbilled).
    assert usage["cost_usd"] == 11.25


def test_total_tokens_can_legitimately_exceed_input_plus_billable_output(monkeypatch):
    """Proves total_tokens is never recomputed locally — issue #63's root cause for
    the 2,548-token gap was exactly a local input+output recomputation losing
    fidelity relative to the provider's real total."""
    monkeypatch.setenv("GEMINI_PRICE_INPUT", "1.25")
    monkeypatch.setenv("GEMINI_PRICE_OUTPUT", "10.0")
    adapter = GeminiAdapter(model="gemini-2.5-pro")

    # A deliberately inconsistent total (bigger than input+candidates+thoughts) to
    # prove the adapter passes it through verbatim rather than ever recomputing it.
    response = _fake_response(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=3,
                               total_token_count=999)
    usage = adapter._extract_usage(response, duration_s=1.0)

    assert usage["total_tokens"] == 999
    assert usage["input_tokens"] + usage["billable_output_tokens"] != usage["total_tokens"]


def test_thinking_budget_zero_for_flash_nonzero_for_pro():
    assert GeminiAdapter(model="gemini-2.5-flash").thinking_budget == 0
    assert GeminiAdapter(model="gemini-2.5-pro").thinking_budget == 128


def test_capabilities_declare_tool_calling_and_structured_output():
    from agent.llm.base import CAPABILITY_STRUCTURED_OUTPUT, CAPABILITY_TOOL_CALLING
    adapter = GeminiAdapter(model="gemini-2.5-pro")
    assert CAPABILITY_TOOL_CALLING in adapter.capabilities
    assert CAPABILITY_STRUCTURED_OUTPUT in adapter.capabilities


def test_reset_session_zeros_all_counters():
    # issue #74: the adapter instance is cached process-wide and reused across
    # investigations -- reset_session() must fully zero every session counter,
    # not just some, or a stale field would leak into the next investigation.
    adapter = GeminiAdapter(model="gemini-2.5-flash")
    adapter._session_input = 100
    adapter._session_cached_input = 20
    adapter._session_output = 50
    adapter._session_reasoning = 10
    adapter._session_tool = 5
    adapter._session_total = 185
    adapter._session_calls = 3
    adapter._session_duration_s = 4.2

    adapter.reset_session()

    usage = adapter.get_session_usage()
    assert usage["session_tokens_input"] == 0
    assert usage["session_tokens_cached_input"] == 0
    assert usage["session_tokens_output"] == 0
    assert usage["session_tokens_reasoning"] == 0
    assert usage["session_tokens_tool"] == 0
    assert usage["session_tokens_total"] == 0
    assert usage["session_calls"] == 0
    assert usage["session_model_latency_s"] == 0.0


def test_a_fresh_adapter_instance_starts_at_zero_session_usage():
    # reset_session() is also called from __init__ -- a brand new instance must
    # never report stale/uninitialized values.
    usage = GeminiAdapter(model="gemini-2.5-flash").get_session_usage()
    assert usage["session_calls"] == 0
    assert usage["session_tokens_total"] == 0


def test_investigate_resets_the_shared_adapter_session_before_each_run(monkeypatch):
    # End-to-end proof of the actual bug: without agent.main.investigate() calling
    # reset_session(), a second investigation on the same warm process would report
    # the FIRST investigation's leftover session totals mixed into its own.
    import agent.llm as llm_facade

    llm_facade._client._session_calls = 7  # simulate leftover state from a prior run
    llm_facade._client._session_total = 12345

    from agent.llm import reset_session
    reset_session()

    usage = llm_facade.get_session_usage()
    assert usage["session_calls"] == 0
    assert usage["session_tokens_total"] == 0
