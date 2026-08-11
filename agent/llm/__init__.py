"""
agent/llm — provider-neutral facade. LangGraph nodes and agent/main.py import
ONLY from this package, never a specific provider SDK or adapter module
directly (issue #63's required design).

The configured adapter is resolved once per process from LLM_PROFILE (see
agent/llm/registry.py), and its required capabilities are validated here at
import time — fail loudly at startup, not deep inside an investigation.
"""
from __future__ import annotations

from agent.llm.base import (
    ALL_CAPABILITIES,
    CAPABILITY_STRUCTURED_OUTPUT,
    CAPABILITY_TOOL_CALLING,
    LLMClient,
    LLMUsage,
    validate_capabilities,
)
from agent.llm.registry import get_client

_client: LLMClient = get_client()

# This LangGraph workflow calls llm_json() everywhere (structured output) and
# is designed around the model driving tool selection (tool calling) — both
# are hard requirements, not optional niceties, so both are validated here.
validate_capabilities(_client, frozenset({CAPABILITY_TOOL_CALLING, CAPABILITY_STRUCTURED_OUTPUT}))

# Backward-compat surface for callers that previously read
# agent.gemini_client.MODEL directly (e.g. the RCA report's model line).
MODEL = _client.model


def llm(system: str, user: str, *, max_tokens: int = 1024) -> tuple[str, LLMUsage]:
    return _client.llm(system, user, max_tokens=max_tokens)


def llm_json(system: str, user: str, *, max_tokens: int = 1024) -> tuple[dict, LLMUsage]:
    return _client.llm_json(system, user, max_tokens=max_tokens)


def get_session_usage() -> dict:
    return _client.get_session_usage()


__all__ = [
    "ALL_CAPABILITIES",
    "CAPABILITY_STRUCTURED_OUTPUT",
    "CAPABILITY_TOOL_CALLING",
    "LLMClient",
    "LLMUsage",
    "MODEL",
    "get_session_usage",
    "llm",
    "llm_json",
    "validate_capabilities",
]
