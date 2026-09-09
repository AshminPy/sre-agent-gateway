"""Section 9 (2026-09-08): 5xx (transient server errors) used to get ZERO retries at
all -- only 429 (rate limit) was ever retried; any 500/503/504 failed the whole
investigation on its first occurrence, identical treatment to a genuinely fatal
error. Extended the same bounded, capped backoff (3 attempts, 30s/60s linear wait)
to 5xx. time.sleep is mocked so this test doesn't actually wait ~90s.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.llm.gemini_adapter import GeminiAdapter


def _adapter():
    adapter = GeminiAdapter(model="gemini-2.5-flash")
    return adapter


def test_503_is_retried_and_eventually_succeeds():
    adapter = _adapter()
    calls = {"n": 0}

    def _generate_content(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("503 Service Unavailable")
        return SimpleNamespace(
            text="{}",
            usage_metadata=SimpleNamespace(
                prompt_token_count=1, cached_content_token_count=0,
                candidates_token_count=1, thoughts_token_count=0,
                tool_use_prompt_token_count=0, total_token_count=2,
            ),
            candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))],
        )

    adapter._client = SimpleNamespace(models=SimpleNamespace(generate_content=_generate_content))

    with patch("time.sleep"):
        response, usage = adapter._call_model("prompt", max_tokens=100)

    assert calls["n"] == 3
    assert response.text == "{}"


def test_500_and_504_are_also_retryable_not_just_503():
    for code in ("500", "504"):
        adapter = _adapter()
        calls = {"n": 0}

        def _generate_content(**kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError(f"{code} Internal error")
            return SimpleNamespace(
                text="{}",
                usage_metadata=SimpleNamespace(
                    prompt_token_count=1, cached_content_token_count=0,
                    candidates_token_count=1, thoughts_token_count=0,
                    tool_use_prompt_token_count=0, total_token_count=2,
                ),
                candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))],
            )

        adapter._client = SimpleNamespace(models=SimpleNamespace(generate_content=_generate_content))
        with patch("time.sleep"):
            response, _ = adapter._call_model("prompt", max_tokens=100)
        assert calls["n"] == 2


def test_5xx_retry_is_still_bounded_at_3_attempts_then_raises():
    adapter = _adapter()
    calls = {"n": 0}

    def _generate_content(**kwargs):
        calls["n"] += 1
        raise RuntimeError("503 Service Unavailable")

    adapter._client = SimpleNamespace(models=SimpleNamespace(generate_content=_generate_content))

    with patch("time.sleep"), pytest.raises(RuntimeError, match="503"):
        adapter._call_model("prompt", max_tokens=100)

    assert calls["n"] == 3  # original + 2 retries, never more


def test_non_retryable_error_fails_immediately_no_wait():
    """A genuinely fatal error (e.g. a 400 bad request) must not be retried at all --
    only the specific transient codes (429/500/503/504) are, per this fix's scope."""
    adapter = _adapter()
    calls = {"n": 0}

    def _generate_content(**kwargs):
        calls["n"] += 1
        raise RuntimeError("400 Bad Request: invalid argument")

    adapter._client = SimpleNamespace(models=SimpleNamespace(generate_content=_generate_content))

    with patch("time.sleep") as mock_sleep, pytest.raises(RuntimeError, match="400"):
        adapter._call_model("prompt", max_tokens=100)

    assert calls["n"] == 1  # no retry at all
    mock_sleep.assert_not_called()
