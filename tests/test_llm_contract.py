"""Adapter contract tests using a fake provider (issue #63 required design).

Proves the LLMClient contract — capability declaration, LLM_PROFILE-based
resolution, startup validation — works for ANY conforming adapter, not just
Gemini. A fake, credential-free provider is the whole point: these tests
must never need real Gemini access.
"""
import pytest

from agent.llm import registry
from agent.llm.base import (
    CAPABILITY_STRUCTURED_OUTPUT,
    CAPABILITY_TOOL_CALLING,
    LLMClient,
    LLMUsage,
    validate_capabilities,
)


class FakeLLMClient(LLMClient):
    """Minimal conforming adapter — no network, no SDK, deterministic output."""

    def __init__(self, model: str, capabilities: frozenset[str] = frozenset({CAPABILITY_TOOL_CALLING, CAPABILITY_STRUCTURED_OUTPUT})):
        self.model = model
        self.capabilities = capabilities
        self.calls = 0

    def llm(self, system: str, user: str, *, max_tokens: int = 1024):
        self.calls += 1
        usage = LLMUsage(
            input_tokens=10, cached_input_tokens=0, output_tokens=5, reasoning_tokens=0,
            tool_tokens=0, total_tokens=15, billable_output_tokens=5, cost_usd=0.0,
            provider="fake", model=self.model, duration_s=0.01,
        )
        return "fake response", usage

    def llm_json(self, system: str, user: str, *, max_tokens: int = 1024):
        text, usage = self.llm(system, user, max_tokens=max_tokens)
        return {"fake": True}, usage

    def get_session_usage(self) -> dict:
        return {"session_calls": self.calls}

    def reset_session(self) -> None:
        self.calls = 0

    def count_tokens(self, text: str) -> int:
        # Deterministic, no network -- good enough for contract tests that don't
        # exercise real context-budget math (those live in test_claim_verifier.py
        # with their own explicit mocks).
        return max(1, len(text) // 4)

    def max_context_tokens(self) -> int:
        return 1_000_000


@pytest.fixture
def fake_registry(monkeypatch):
    """Registers 'fake' under the registry without touching the real 'gemini'
    registration, and guarantees cleanup so no state leaks into other tests."""
    saved_adapters = dict(registry._ADAPTERS)
    registry.register_adapter("fake", lambda profile: FakeLLMClient(model=profile))
    registry.reset_for_tests()
    yield
    registry._ADAPTERS.clear()
    registry._ADAPTERS.update(saved_adapters)
    registry.reset_for_tests()


def test_registry_resolves_the_configured_profile_to_the_right_adapter(fake_registry, monkeypatch):
    monkeypatch.setenv("LLM_PROFILE", "fake-v1")
    client = registry.get_client()
    assert isinstance(client, FakeLLMClient)
    assert client.model == "fake-v1"


def test_registry_caches_the_client_across_calls(fake_registry, monkeypatch):
    monkeypatch.setenv("LLM_PROFILE", "fake-v1")
    first = registry.get_client()
    second = registry.get_client()
    assert first is second


def test_registry_re_resolves_when_profile_changes(fake_registry, monkeypatch):
    monkeypatch.setenv("LLM_PROFILE", "fake-v1")
    first = registry.get_client()
    monkeypatch.setenv("LLM_PROFILE", "fake-v2")
    second = registry.get_client()
    assert first is not second
    assert second.model == "fake-v2"


def test_unrecognized_profile_raises_a_clear_error(fake_registry, monkeypatch):
    monkeypatch.setenv("LLM_PROFILE", "unknown-provider-xyz")
    with pytest.raises(ValueError, match="does not match any registered adapter"):
        registry.get_client()


def test_validate_capabilities_passes_when_all_required_are_present():
    client = FakeLLMClient(model="fake-v1")
    validate_capabilities(client, frozenset({CAPABILITY_TOOL_CALLING, CAPABILITY_STRUCTURED_OUTPUT}))


def test_validate_capabilities_fails_loudly_when_a_required_capability_is_missing():
    client = FakeLLMClient(model="fake-v1", capabilities=frozenset({CAPABILITY_TOOL_CALLING}))
    with pytest.raises(RuntimeError, match="missing required capabilities"):
        validate_capabilities(client, frozenset({CAPABILITY_TOOL_CALLING, CAPABILITY_STRUCTURED_OUTPUT}))


def test_fake_adapter_llm_json_returns_a_well_formed_llmusage():
    client = FakeLLMClient(model="fake-v1")
    _, usage = client.llm_json("system", "user")
    required_keys = {
        "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
        "tool_tokens", "total_tokens", "billable_output_tokens", "cost_usd",
        "provider", "model", "duration_s",
    }
    assert required_keys <= set(usage.keys())
    assert usage["billable_output_tokens"] == usage["output_tokens"] + usage["reasoning_tokens"]
