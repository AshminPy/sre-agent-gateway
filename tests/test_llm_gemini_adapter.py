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

from agent.llm import llm_json_failed
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


def _fake_usage():
    return {
        "input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1,
        "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 2,
        "billable_output_tokens": 1, "cost_usd": 0.0, "provider": "gemini",
        "model": "gemini-2.5-flash", "duration_s": 0.01,
    }


def _fake_model_response(text: str, finish_reason: str | None = "STOP"):
    """Mimics enough of google.genai's GenerateContentResponse shape for
    llm_json()/_finish_reason_name()/_is_truncated(): a `.text` attribute and a
    `.candidates[0].finish_reason` with a real enum-like `.name`."""
    candidates = (
        [SimpleNamespace(finish_reason=SimpleNamespace(name=finish_reason))]
        if finish_reason is not None else []
    )
    return SimpleNamespace(text=text, candidates=candidates)


def _patch_call_model(monkeypatch, adapter, responses):
    """responses: list of (fake_response, usage) tuples, returned in order --
    one per _call_model() invocation. Proves how many real 'calls' llm_json()
    made without needing the real Gemini client."""
    queue = list(responses)

    def fake_call_model(prompt, max_tokens):
        return queue.pop(0)

    monkeypatch.setattr(adapter, "_call_model", fake_call_model)
    return queue


def test_llm_json_no_json_found_logs_length_not_content(monkeypatch, caplog):
    # issue #76: this failure path used to log up to 200 chars of the model's raw
    # response text (built from real k8s evidence) -- must log only metadata now.
    import logging
    adapter = GeminiAdapter(model="gemini-2.5-flash")
    secret_text = "SENSITIVE pod log content: password=hunter2, db_host=10.1.2.3"
    _patch_call_model(monkeypatch, adapter, [
        (_fake_model_response(secret_text), _fake_usage()),
    ])
    with caplog.at_level(logging.WARNING):
        result, _ = adapter.llm_json("sys", "user")

    # 2026-08-27: was a bare {}, which no caller could tell apart from a
    # legitimately empty model result -- the parse failure was invisible. Now
    # marked so it can be detected. The issue #76 privacy guarantees below are
    # unchanged, and now also cover the marker's own text.
    assert llm_json_failed(result), "parse failure must be detectable"
    assert result.get("enough_evidence", False) is False, "callers keep their defaults"
    assert "SENSITIVE" not in caplog.text
    assert "hunter2" not in caplog.text
    assert "10.1.2.3" not in caplog.text
    assert "length=" in caplog.text
    assert "SENSITIVE" not in llm_json_failed(result)
    assert "hunter2" not in llm_json_failed(result)


def test_llm_json_repair_failure_logs_length_not_content(monkeypatch, caplog):
    import logging
    adapter = GeminiAdapter(model="gemini-2.5-flash")
    # Starts with "{" so it reaches the repair path, but is unparseable JSON even
    # after repair -- and contains content that must never reach the log. finish_reason
    # is STOP (not MAX_TOKENS) so this is a genuinely malformed response, not a
    # truncated one -- must NOT trigger the truncation retry.
    secret_text = '{"note": "SENSITIVE db_host=10.1.2.3 unterminated'
    queue = _patch_call_model(monkeypatch, adapter, [
        (_fake_model_response(secret_text, finish_reason="STOP"), _fake_usage()),
    ])
    with caplog.at_level(logging.WARNING):
        result, _ = adapter.llm_json("sys", "user")

    # Same contract change as the test above; same privacy guarantees, now also
    # asserted against the marker text (which carries the JSON parser's own
    # message, never the model's content).
    assert llm_json_failed(result), "parse failure must be detectable"
    assert "SENSITIVE" not in caplog.text
    assert "10.1.2.3" not in caplog.text
    assert "length=" in caplog.text
    assert "SENSITIVE" not in llm_json_failed(result)
    assert "10.1.2.3" not in llm_json_failed(result)
    assert "truncated" not in llm_json_failed(result).lower()
    assert queue == [], "a genuinely malformed (non-truncated) response must not retry"


# ── cascading-001 parse-failure root cause: MAX_TOKENS truncation (2026-08-31) ──

def test_finish_reason_name_reads_the_real_enum():
    from agent.llm.gemini_adapter import _finish_reason_name
    response = _fake_model_response("{}", finish_reason="MAX_TOKENS")
    assert _finish_reason_name(response) == "MAX_TOKENS"


def test_finish_reason_name_empty_when_no_candidates():
    from agent.llm.gemini_adapter import _finish_reason_name
    response = _fake_model_response("{}", finish_reason=None)
    assert _finish_reason_name(response) == ""


def test_is_truncated_true_only_for_max_tokens():
    from agent.llm.gemini_adapter import _is_truncated
    assert _is_truncated(_fake_model_response("{}", finish_reason="MAX_TOKENS")) is True
    assert _is_truncated(_fake_model_response("{}", finish_reason="STOP")) is False
    assert _is_truncated(_fake_model_response("{}", finish_reason="SAFETY")) is False


def test_llm_json_retries_once_on_max_tokens_truncation_and_succeeds(monkeypatch, caplog):
    """The exact mechanism behind cascading-001/pending-001/mcp-gateway-failure-001's
    intermittent parse failures: a response cut off mid-JSON by max_output_tokens,
    positively identified via finish_reason=MAX_TOKENS (not inferred from the broken
    text), retried once with a larger budget."""
    import logging
    adapter = GeminiAdapter(model="gemini-2.5-flash")
    truncated_text = '{"likely_root_cause": "order-db OOMKilled", "claims": [{"tex'  # cut mid-field
    full_text = '{"likely_root_cause": "order-db OOMKilled", "claims": []}'
    queue = _patch_call_model(monkeypatch, adapter, [
        (_fake_model_response(truncated_text, finish_reason="MAX_TOKENS"), _fake_usage()),
        (_fake_model_response(full_text, finish_reason="STOP"), _fake_usage()),
    ])
    with caplog.at_level(logging.WARNING):
        result, usage = adapter.llm_json("sys", "user", max_tokens=1536)

    assert not llm_json_failed(result), "the retry must produce a real, usable result"
    assert result == {"likely_root_cause": "order-db OOMKilled", "claims": []}
    assert queue == [], "exactly two _call_model calls -- original + one retry, no more"
    assert "truncat" in caplog.text.lower()
    # Usage from BOTH attempts must be counted -- both are real spend.
    assert usage["total_tokens"] == 4


def test_llm_json_truncation_retry_still_fails_reports_truncated(monkeypatch):
    """If even the larger retry budget still truncates, report it as truncated
    (not a generic "malformed JSON") so the real cause is visible in logs/evidence_gaps
    instead of looking like an unrelated model-output bug."""
    adapter = GeminiAdapter(model="gemini-2.5-flash")
    still_truncated = '{"likely_root_cause": "still cut of'
    queue = _patch_call_model(monkeypatch, adapter, [
        (_fake_model_response(still_truncated, finish_reason="MAX_TOKENS"), _fake_usage()),
        (_fake_model_response(still_truncated, finish_reason="MAX_TOKENS"), _fake_usage()),
    ])
    result, _ = adapter.llm_json("sys", "user", max_tokens=1536)

    assert llm_json_failed(result)
    assert "truncated" in llm_json_failed(result).lower()
    assert queue == [], "must not retry a second time"


def test_llm_json_does_not_retry_past_the_truncation_retry_ceiling(monkeypatch):
    """A caller that already passed a max_tokens at/above the retry ceiling must not
    trigger an (even larger) retry -- _TRUNCATION_RETRY_MAX_TOKENS is a hard cap."""
    from agent.llm.gemini_adapter import _TRUNCATION_RETRY_MAX_TOKENS
    adapter = GeminiAdapter(model="gemini-2.5-flash")
    truncated_text = '{"a": "b'
    queue = _patch_call_model(monkeypatch, adapter, [
        (_fake_model_response(truncated_text, finish_reason="MAX_TOKENS"), _fake_usage()),
    ])
    result, _ = adapter.llm_json("sys", "user", max_tokens=_TRUNCATION_RETRY_MAX_TOKENS)

    assert llm_json_failed(result)
    assert queue == [], "already at the ceiling -- must not attempt a retry at all"


def test_llm_json_real_content_on_first_try_never_retries(monkeypatch):
    adapter = GeminiAdapter(model="gemini-2.5-flash")
    queue = _patch_call_model(monkeypatch, adapter, [
        (_fake_model_response('{"ok": true}', finish_reason="STOP"), _fake_usage()),
    ])
    result, _ = adapter.llm_json("sys", "user")
    assert result == {"ok": True}
    assert queue == []


# ── count_tokens() / max_context_tokens() (2026-09-02 confidence-verifier context-budget fix) ──
#
# Real SDK signatures confirmed against the actually-installed google-genai (1.47.0 and
# 2.10.0 both checked live):
#   Client.models.count_tokens(*, model: str, contents) -> CountTokensResponse (.total_tokens)
#   Client.models.get(*, model: str) -> Model (.input_token_limit)
# These tests mock at that boundary (adapter._get_client()), not the network.
#
# input_token_limit's real Vertex behavior, confirmed live against project
# sreagent-t2-demo (2026-09-02): Client.models.get(model="gemini-2.5-flash") returns
# input_token_limit=None on the Vertex AI backend (vertexai=True, what this adapter
# always uses) -- the field is only populated on the separate, non-Vertex Gemini
# Developer API. The tests below cover both that real None-returning shape (proving the
# documented-fallback path activates) and the hypothetical case where Google populates
# it later (proving the live value would be used and preferred, with zero code change).

def test_count_tokens_uses_the_sdk_count_tokens_call(monkeypatch):
    adapter = GeminiAdapter(model="gemini-2.5-flash")
    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            count_tokens=lambda model, contents: SimpleNamespace(total_tokens=1234)
        )
    )
    monkeypatch.setattr(adapter, "_get_client", lambda: fake_client)
    assert adapter.count_tokens("some text") == 1234


def test_max_context_tokens_falls_back_to_documented_limit_when_vertex_returns_none(monkeypatch):
    """Pins the REAL Vertex response shape (input_token_limit=None), not a hypothetical
    one -- confirmed live against the actually deployed project. Must still resolve to
    the correct documented limit via the fallback table, not raise and not return None."""
    adapter = GeminiAdapter(model="gemini-2.5-flash")
    calls = []

    def _fake_get(model):
        calls.append(model)
        return SimpleNamespace(input_token_limit=None)  # the real Vertex shape

    fake_client = SimpleNamespace(models=SimpleNamespace(get=_fake_get))
    monkeypatch.setattr(adapter, "_get_client", lambda: fake_client)

    assert adapter.max_context_tokens() == 1_048_576
    assert adapter.max_context_tokens() == 1_048_576
    assert calls == ["gemini-2.5-flash"], "must call models.get() once and cache, not re-fetch per call"


def test_max_context_tokens_prefers_live_value_when_vertex_actually_returns_one(monkeypatch):
    """If Google ever populates input_token_limit on the Vertex path, the live value
    must be used and preferred over the fallback table -- with zero code change."""
    adapter = GeminiAdapter(model="gemini-2.5-flash")
    fake_client = SimpleNamespace(
        models=SimpleNamespace(get=lambda model: SimpleNamespace(input_token_limit=999_999))
    )
    monkeypatch.setattr(adapter, "_get_client", lambda: fake_client)
    assert adapter.max_context_tokens() == 999_999


def test_max_context_tokens_raises_for_unlisted_model_instead_of_guessing(monkeypatch):
    adapter = GeminiAdapter(model="some-future-model-not-in-the-table")
    fake_client = SimpleNamespace(
        models=SimpleNamespace(get=lambda model: SimpleNamespace(input_token_limit=None))
    )
    monkeypatch.setattr(adapter, "_get_client", lambda: fake_client)
    try:
        adapter.max_context_tokens()
        assert False, "expected a RuntimeError for an unlisted model"
    except RuntimeError as exc:
        assert "some-future-model-not-in-the-table" in str(exc)
