"""
agent/llm/registry.py — resolves LLM_PROFILE to a configured adapter instance.

Only "gemini" is registered today (issue #63 PR 1 explicitly implements only
the Gemini adapter — no unused providers). Adding a second provider later is:
  1. Write its adapter module (own LLMClient subclass) + wire its credentials
     and pricing source.
  2. register_adapter() it here under its own prefix.
  3. Change LLM_PROFILE. The LangGraph workflow (agent/nodes/*, agent/main.py)
     never changes — it only ever imports from agent.llm's facade.

A true one-variable switch (step 3 alone, no code change) is only real once
steps 1-2 have already been done for the target provider — this registry
does not pretend otherwise.
"""
from __future__ import annotations

import os
from typing import Callable

from agent.llm.base import LLMClient

# prefix -> factory(model_or_profile: str) -> LLMClient. "gemini" matches any
# LLM_PROFILE starting with "gemini-" (e.g. "gemini-2.5-pro"), and the whole
# profile string doubles as the exact model name the Gemini API expects —
# no separate provider/model split needed for this one adapter.
_ADAPTERS: dict[str, Callable[[str], LLMClient]] = {}

_client: LLMClient | None = None
_resolved_profile: str | None = None


def register_adapter(prefix: str, factory: Callable[[str], LLMClient]) -> None:
    """Registers a provider adapter under a profile-string prefix. Production
    code calls this once per real provider (see the bottom of this module for
    Gemini's registration); tests use it to register a fake provider without
    needing real credentials.
    """
    _ADAPTERS[prefix] = factory


def _resolve_profile() -> str:
    # LLM_PROFILE is the new, provider-neutral selector. Falls back to the
    # existing GEMINI_MODEL (already set in Terraform/CI today) so this works
    # in production with zero infra changes until LLM_PROFILE is wired
    # through Terraform explicitly as its own follow-up.
    return os.environ.get("LLM_PROFILE") or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


def get_client() -> LLMClient:
    """Returns the process-wide adapter instance for the configured LLM_PROFILE,
    resolving and caching it on first call."""
    global _client, _resolved_profile

    profile = _resolve_profile()
    if _client is not None and _resolved_profile == profile:
        return _client

    for prefix, factory in _ADAPTERS.items():
        if profile.startswith(f"{prefix}-") or profile == prefix:
            _client = factory(profile)
            _resolved_profile = profile
            return _client

    raise ValueError(
        f"LLM_PROFILE='{profile}' does not match any registered adapter "
        f"(registered: {sorted(_ADAPTERS)}). Add the adapter first, per this "
        f"module's docstring, before pointing LLM_PROFILE at it."
    )


def reset_for_tests() -> None:
    """Test-only: clears the cached client so a test can register a fake
    adapter and force re-resolution."""
    global _client, _resolved_profile
    _client = None
    _resolved_profile = None


def _register_gemini() -> None:
    from agent.llm.gemini_adapter import GeminiAdapter
    register_adapter("gemini", lambda profile: GeminiAdapter(model=profile))


_register_gemini()
