"""Regression test: a blocked LLM call must raise a clear error, not an AttributeError.

Real production failure, 2026-08-26T21:48:11Z. Model Armor's AI_PLATFORM floor
setting was running inspect_and_block=true. Its pi_and_jailbreak filter matched
(MEDIUM_AND_ABOVE) on the agent's OWN static system prompt:

    "You are an SRE evidence analyst. Extract the most investigation-relevant
     facts from tool output. Focus on: failures, errors, restart counts, exit
     codes, OOM kills, image pull errors, scheduling failures, warning events."

Every other filter returned NO_MATCH_FOUND. Nothing malicious in the text.

The call was blocked, Gemini returned a response with no text part, and
`response.text.strip()` raised:

    'NoneType' object has no attribute 'strip'

The whole investigation died with {"error": "'NoneType' object has no attribute
'strip'", "status": "failed"} -- an error that points at nothing and sends the
next reader into the wrong codebase entirely.

The fix does NOT return an empty string. Returning "" would let a downstream node
treat a blocked call as a real (empty) answer -- the same class of bug as PR #199,
where a failure was quietly presented as a success.
"""
import pytest

from agent.llm.gemini_adapter import GeminiAdapter


class _FakeUsageMetadata:
    prompt_token_count = 10
    candidates_token_count = 0
    total_token_count = 10
    cached_content_token_count = 0
    thoughts_token_count = 0
    tool_use_prompt_token_count = 0


class _Response:
    def __init__(self, text):
        self.text = text
        self.usage_metadata = _FakeUsageMetadata()
        self.candidates = []


class _FakeModels:
    def __init__(self, response):
        self._response = response

    def generate_content(self, **kwargs):
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.models = _FakeModels(response)


def _adapter(monkeypatch, response):
    """A real GeminiAdapter with only the SDK client and span emitter faked."""
    adapter = GeminiAdapter.__new__(GeminiAdapter)
    for attr in (
        "_session_input", "_session_cached_input", "_session_output",
        "_session_reasoning", "_session_tool", "_session_total",
        "_session_calls", "_session_duration_s",
    ):
        setattr(adapter, attr, 0)
    adapter.price_input_per_1m = 0.0
    adapter.price_output_per_1m = 0.0
    adapter.model = "gemini-2.5-pro"
    adapter.thinking_budget = 0
    monkeypatch.setattr(
        GeminiAdapter, "_get_client", lambda self: _FakeClient(response), raising=False
    )
    monkeypatch.setattr(
        GeminiAdapter, "_emit_gen_ai_span", lambda self, *a, **k: None, raising=False
    )
    return adapter


SRE_PROMPT = (
    "You are an SRE evidence analyst. Extract the most investigation-relevant "
    "facts from tool output."
)


def test_blocked_response_raises_a_message_that_names_the_cause(monkeypatch):
    adapter = _adapter(monkeypatch, _Response(None))
    with pytest.raises(RuntimeError) as excinfo:
        adapter.llm(SRE_PROMPT, "pod imagepull-pod in test-incidents")
    msg = str(excinfo.value)
    assert "returned no text" in msg
    assert "sanitize_operations" in msg  # points the reader at the real evidence
    assert "NoneType" not in msg


def test_blocked_response_never_returns_an_empty_string(monkeypatch):
    """An empty answer would be indistinguishable from a real one downstream."""
    adapter = _adapter(monkeypatch, _Response(None))
    with pytest.raises(RuntimeError):
        adapter.llm(SRE_PROMPT, "pod imagepull-pod in test-incidents")


def test_a_normal_response_is_unaffected(monkeypatch):
    """Guard against the None check breaking healthy calls."""
    adapter = _adapter(monkeypatch, _Response("  a real answer  "))
    text, usage = adapter.llm(SRE_PROMPT, "pod imagepull-pod in test-incidents")
    assert text == "a real answer"
    assert usage["input_tokens"] == 10
