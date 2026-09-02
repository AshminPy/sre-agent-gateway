"""
agent/llm/base.py — provider-neutral LLM client contract.

LangGraph nodes import from agent.llm (the facade in __init__.py), never a
specific provider SDK or module directly. This file defines the shape every
provider adapter must satisfy: a normalized usage schema (LLMUsage) and an
abstract client interface (LLMClient) with a declared capability set.

Design note (issue #63): a real bug shipped because token accounting lived
ad-hoc in every LangGraph node and in the Gemini SDK wrapper, with no single
place enforcing consistency. This module exists so "what does a call cost,
in normalized terms" has exactly one contract, regardless of which provider
answers it.
"""
from __future__ import annotations

import abc
from typing import TypedDict


class LLMUsage(TypedDict):
    """Normalized usage for one LLM call, common across providers.

    Provider-specific granularity is preserved, not collapsed: Gemini's
    candidates_token_count and thoughts_token_count map to output_tokens and
    reasoning_tokens respectively and stay separate fields — never summed
    into one bucket — so a caller that cares about the breakdown still can.

    billable_output_tokens = output_tokens + reasoning_tokens. Reasoning/
    thinking tokens bill at the output rate (confirmed for Gemini 2.5 against
    Google's own Vertex AI pricing page: the output rate covers "Text output
    tokens (response and reasoning)") — this is the field cost calculation
    must use, not output_tokens alone.

    total_tokens is the PROVIDER'S OWN reported total (Gemini: total_token_count)
    — never locally recomputed as input + output. A provider's real total can
    legitimately include token categories a caller doesn't otherwise see
    (issue #63's root cause for the 2,548-token gap was exactly this kind of
    silent local recomputation losing fidelity).
    """

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    tool_tokens: int
    total_tokens: int
    billable_output_tokens: int
    cost_usd: float
    provider: str
    model: str
    duration_s: float


# Capability identifiers. A LangGraph node that requires structured JSON
# output or tool-calling support declares that requirement via these
# constants — never a provider-specific string — so validate_capabilities()
# stays meaningful for any future adapter.
CAPABILITY_TOOL_CALLING = "tool_calling"
CAPABILITY_STRUCTURED_OUTPUT = "structured_output"

ALL_CAPABILITIES = frozenset({CAPABILITY_TOOL_CALLING, CAPABILITY_STRUCTURED_OUTPUT})

# 2026-08-27: llm_json() returns {} when the model's response cannot be parsed
# as JSON at all. That was indistinguishable from "the model legitimately
# returned an empty object", so a parse failure was invisible: every caller
# silently fell back to its own defaults and nothing was ever recorded in
# state["errors"]. Adapters now set this key on that path instead of returning a
# bare {}.
#
# Deliberately additive rather than a signature change: all six call sites read
# specific keys with defaults (result.get("enough_evidence", False) and
# friends), so they keep working unchanged while gaining the ability to detect
# the failure. Callers that care use llm_json_failed() from agent.llm.
LLM_JSON_PARSE_FAILED_KEY = "__llm_json_parse_failed__"


class LLMClient(abc.ABC):
    """Provider adapter contract. One instance per configured LLM_PROFILE."""

    #: Capabilities this adapter actually supports — checked once at startup
    #: via validate_capabilities(), not re-checked on every call.
    capabilities: frozenset[str] = frozenset()

    @abc.abstractmethod
    def llm(self, system: str, user: str, *, max_tokens: int = 1024) -> tuple[str, LLMUsage]:
        """Call the model and return (text, usage)."""

    @abc.abstractmethod
    def llm_json(self, system: str, user: str, *, max_tokens: int = 1024) -> tuple[dict, LLMUsage]:
        """Call the model and return (parsed_json, usage)."""

    @abc.abstractmethod
    def get_session_usage(self) -> dict:
        """Cumulative usage since the last reset_session() call.

        The adapter instance is cached process-wide (agent.llm.registry.get_client()),
        reused across every investigation a warm process handles — this was ungated
        cumulative-forever state until issue #74's fix. Callers MUST call
        reset_session() at the start of each investigation (agent/main.py's
        investigate() does this) for this to mean "this investigation's usage"
        rather than "everything since process start."
        """

    @abc.abstractmethod
    def reset_session(self) -> None:
        """Zero all session counters. Call once at the start of each investigation
        (issue #74) -- without this, get_session_usage() accumulates across every
        investigation a warm/reused process handles, not just the current one."""

    @abc.abstractmethod
    def count_tokens(self, text: str) -> int:
        """Real token count for `text`, via the provider's own counting capability
        (2026-09-02, confidence-verifier context-budget fix). Callers needing to know
        whether a request fits a model's context window must use this -- never a
        character-length proxy, which has no defined relationship to how any given
        model actually tokenizes text."""

    @abc.abstractmethod
    def max_context_tokens(self) -> int:
        """This model's documented input-token capacity, reported by the provider
        itself -- never a value hardcoded by a caller. Static per model; adapters
        should cache it rather than re-fetching on every call."""


def validate_capabilities(client: LLMClient, required: frozenset[str]) -> None:
    """Fail loudly at startup if the configured adapter can't do what this
    LangGraph workflow structurally needs — never a silent degrade at call
    time deep in an investigation.
    """
    missing = required - client.capabilities
    if missing:
        raise RuntimeError(
            f"LLM adapter '{type(client).__name__}' is missing required "
            f"capabilities: {sorted(missing)}. This workflow requires: "
            f"{sorted(required)}."
        )
