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
    LLM_JSON_PARSE_FAILED_KEY,
    LLMClient,
    LLMUsage,
    validate_capabilities,
)


def llm_json_failed(result: dict) -> str:
    """Returns the failure reason when llm_json() could not parse the model's
    response, or "" when the result is a real parse.

    Use this anywhere a caller would otherwise mistake an unparseable model
    response for a legitimately empty one. See LLM_JSON_PARSE_FAILED_KEY.
    """
    if not isinstance(result, dict):
        return "llm_json returned a non-dict result"
    return str(result.get(LLM_JSON_PARSE_FAILED_KEY) or "")
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


def reset_session() -> None:
    """issue #74: call once at the start of every investigation (agent/main.py's
    investigate() does this) -- the adapter instance is cached process-wide, so
    without this reset, get_session_usage() accumulates across every investigation
    a warm/reused process handles, not just the current one."""
    _client.reset_session()


__all__ = [
    "ALL_CAPABILITIES",
    "CAPABILITY_STRUCTURED_OUTPUT",
    "CAPABILITY_TOOL_CALLING",
    "LLM_JSON_PARSE_FAILED_KEY",
    "llm_json_failed",
    "LLMClient",
    "LLMUsage",
    "MODEL",
    "get_session_usage",
    "llm",
    "llm_json",
    "reset_session",
    "validate_capabilities",
]
