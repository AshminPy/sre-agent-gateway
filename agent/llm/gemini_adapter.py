"""
agent/llm/gemini_adapter.py — the only place that imports the Gemini/Vertex AI SDK.

Everything Gemini-specific lives here: the google-genai client, thinking-budget
handling, usage_metadata field extraction, and (for now, per issue #63 PR 1 —
dynamic pricing is PR 2) the existing Terraform-sourced static pricing.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
import time

import certifi
from google import genai
from google.genai import types

from agent.llm.base import (
    CAPABILITY_STRUCTURED_OUTPUT,
    CAPABILITY_TOOL_CALLING,
    LLMClient,
    LLMUsage,
)

log = logging.getLogger("sre-agent.llm.gemini")


def _finish_reason_name(response) -> str:
    """response.candidates[0].finish_reason as a plain string, or "" if unavailable.

    Never raises -- candidates can legitimately be empty/absent (e.g. the
    response.text is None / blocked-call path above already handles that
    case separately). This is a read-only diagnostic accessor only.
    """
    try:
        candidates = getattr(response, "candidates", None)
        if not candidates:
            return ""
        reason = getattr(candidates[0], "finish_reason", None)
        return reason.name if hasattr(reason, "name") else str(reason or "")
    except Exception:
        return ""


def _is_truncated(response) -> bool:
    """True when Gemini stopped generating because it hit max_output_tokens --
    the provider's own explicit signal (FinishReason.MAX_TOKENS), not an
    inference from the malformed text itself.

    2026-08-31: added to root-cause the intermittent "model's response could
    not be parsed" failure (cascading-001, pending-001, mcp-gateway-failure-001
    -- see docs/management/confidence-genericity-review-2026-08-28.md). Before
    this, a truncated response (cut off mid-string/mid-object) and a genuinely
    malformed one were indistinguishable -- both just failed
    json.loads()/the brace-repair pass below, which CANNOT recover an
    unterminated string or unbalanced braces (that isn't a parser
    shortcoming, it's not enough information to reconstruct). Only the
    higher-complexity multi-hop cases (more claims/hypotheses -> longer JSON)
    ever hit this, never the simple single-cause cases -- consistent with an
    output-length problem, not a random API/format glitch.
    """
    return _finish_reason_name(response) == "MAX_TOKENS"


# Hard ceiling for the one-shot truncation retry below -- never grows without
# bound even if a caller passes an unusually large max_tokens already.
_TRUNCATION_RETRY_MAX_TOKENS = 8192

# Fallback ONLY -- max_context_tokens() always tries the live
# Client.models.get(...).input_token_limit first. This table exists because that field
# is confirmed (2026-09-02) to always be None for Gemini models on the Vertex AI
# backend, in both the google-genai SDK's Vertex response mapper and the
# aiplatform_v1beta1 ModelGardenService.get_publisher_model() response -- unlike the
# separate, non-Vertex Gemini Developer (ai.google.dev, API-key) surface, which does
# populate it. Values are Google's own documented limits, not guessed:
#   https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash
#   https://ai.google.dev/gemini-api/docs/models/gemini-2.5-pro
# A model not listed here raises in max_context_tokens() rather than silently
# defaulting to a guessed number.
_VERTEX_INPUT_TOKEN_LIMIT_FALLBACK: dict[str, int] = {
    "gemini-2.5-flash": 1_048_576,
    "gemini-2.5-pro": 1_048_576,
}


_EMPTY_SESSION = {
    "input": 0, "cached_input": 0, "output": 0, "reasoning": 0,
    "tool": 0, "total": 0, "calls": 0, "duration_s": 0.0,
}


class GeminiAdapter(LLMClient):
    """Vertex AI Gemini adapter. Model is fixed per instance (one adapter = one
    resolved LLM_PROFILE for the process lifetime — matches Agent Engine's
    single-model-per-deployment reality; nothing here switches models mid-run).

    Section 9 (2026-09-08): session/usage counters live in a contextvars.ContextVar,
    not plain instance attributes. Confirmed real gap via audit: this ONE adapter
    instance is cached process-wide (agent.llm.registry's module-level singleton) and
    shared across every investigation a warm container handles. Plain instance
    attributes would let two concurrent investigations on the same warm instance
    interleave -- investigation B's reset_session() zeroing investigation A's
    in-flight counters, and both investigations' usage summing into one shared total
    attributable to neither correctly (docs/architecture/agent-engine.md's own
    "flag as UNKNOWN, not independently load-tested" note, closed here rather than
    left open). ContextVar isolates per asyncio task AND per native thread by
    Python's own design -- the standard mechanism for exactly this problem, not a
    custom one invented here.
    """

    capabilities = frozenset({CAPABILITY_TOOL_CALLING, CAPABILITY_STRUCTURED_OUTPUT})

    def __init__(self, model: str):
        self.model = model
        self.project_id = os.environ.get("PROJECT_ID", "your-gcp-project-id")
        self.region = os.environ.get("REGION", "us-central1")

        # Flash/Flash-Lite allow thinking_budget=0 (fully disabled). Pro models reject 0
        # ("The model does not support setting thinking_budget to 0.") and require
        # 128-32768 (or -1 for dynamic) — 128 is the minimum, used here to stay as
        # close to deterministic as this model allows.
        self.thinking_budget = 128 if "pro" in model else 0

        # Vertex AI model-endpoint location. Reads GOOGLE_CLOUD_LOCATION, defaulting to
        # REGION (the official codelab uses the regional endpoint and sets no global
        # model-endpoint-location).
        self.model_endpoint_location = os.environ.get("GOOGLE_CLOUD_LOCATION", self.region)

        # Pricing for cost estimation — PR 1 (this) keeps the existing Terraform-sourced
        # static rate unchanged (iac/agent/variables.tf's gemini_price_input_per_1m/
        # gemini_price_output_per_1m -> GEMINI_PRICE_INPUT/GEMINI_PRICE_OUTPUT). PR 2
        # replaces this with a fetched, cached, versioned Cloud Billing Catalog snapshot
        # behind this same adapter — nothing outside this class needs to change when
        # that lands. Fallback stays an obviously-wrong 0.0, never a guessed price (see
        # issue #63's original root cause: a silently-plausible Flash-rate fallback).
        self.price_input_per_1m = float(os.environ.get("GEMINI_PRICE_INPUT", "0.0"))
        self.price_output_per_1m = float(os.environ.get("GEMINI_PRICE_OUTPUT", "0.0"))

        self._client = None
        # Static per model/deployment -- fetched once via the real SDK on first use,
        # never re-derived per call. Not reset by reset_session() (that zeroes
        # per-investigation usage counters; this is model metadata, not usage).
        self._max_context_tokens: int | None = None
        # One ContextVar per adapter instance (there is exactly one live instance --
        # agent.llm.registry's cached singleton -- but scoping it to the instance
        # rather than the module avoids any cross-instance leakage in tests that
        # construct more than one adapter).
        self._session_var: contextvars.ContextVar = contextvars.ContextVar(
            f"gemini_session_{id(self)}"
        )
        self.reset_session()

    def reset_session(self) -> None:
        # issue #74 / Section 9 (2026-09-08): this instance is cached process-wide
        # (agent.llm.registry) and reused across every investigation a warm process
        # handles. agent/main.py's investigate() calls this once at the start of
        # every investigation so get_session_usage() means "this investigation," not
        # "everything since process start" -- and, as of this fix, not "whatever
        # concurrent investigation happens to be running on the same warm instance"
        # either: this sets a NEW dict in the CURRENT context only, never mutates a
        # shared one, so a concurrent investigation's own reset_session() (a
        # different context) cannot zero this one's in-flight counters.
        self._session_var.set(dict(_EMPTY_SESSION))

    def _session(self) -> dict:
        try:
            return self._session_var.get()
        except LookupError:
            # Defensive only -- every real call path goes through __init__ (which
            # calls reset_session()) first. Never silently returns a shared mutable
            # default; sets and returns a fresh one scoped to THIS context.
            session = dict(_EMPTY_SESSION)
            self._session_var.set(session)
            return session

    def _get_client(self):
        if self._client is None:
            # No base_url override. google-genai hardcodes the plain endpoint
            # (aiplatform.googleapis.com) with no mTLS branch at all — verified
            # directly against the installed source (_api_client.py), so an mTLS
            # hostname substitution, if it happens, happens below this library,
            # not because of anything set here. See archive/RESOLVED_2026-07-17_TROUBLESHOOTING_LOG.md,
            # 2026-07-16, for the full evidence trail.
            self._client = genai.Client(
                vertexai=True,
                project=self.project_id,
                location=self.model_endpoint_location,
            )
            log.info(
                "Gemini client initialized via Vertex AI: %s in %s/%s",
                self.model, self.project_id, self.model_endpoint_location,
            )
            try:
                resolved_base_url = getattr(
                    getattr(self._client, "_api_client", None), "_http_options", None
                )
                resolved_base_url = getattr(resolved_base_url, "base_url", "unknown")
                log.info(
                    "[mtls-diag] python=%s resolved_base_url=%s "
                    "GOOGLE_API_USE_MTLS_ENDPOINT=%s GOOGLE_API_USE_CLIENT_CERTIFICATE=%s "
                    "certifi.where()=%s",
                    sys.version.split()[0],
                    resolved_base_url,
                    os.environ.get("GOOGLE_API_USE_MTLS_ENDPOINT", "<unset>"),
                    os.environ.get("GOOGLE_API_USE_CLIENT_CERTIFICATE", "<unset>"),
                    certifi.where(),
                )
            except Exception as exc:
                log.warning("[mtls-diag] failed to collect diagnostics: %s", exc)
        return self._client

    def _calculate_cost(self, billable_output_tokens: int, input_tokens: int) -> float:
        return round(
            (input_tokens / 1_000_000) * self.price_input_per_1m
            + (billable_output_tokens / 1_000_000) * self.price_output_per_1m,
            6,
        )

    def _emit_gen_ai_span(self, usage: LLMUsage, max_tokens: int, span_start_ns: int, span_end_ns: int) -> None:
        """Emit a gen_ai.* OTEL span so Agent Platform Models/Usage tabs show data."""
        try:
            from agent.otel import get_tracer, set_span_attributes
            from opentelemetry import trace as _ot
            tracer = get_tracer()
            if not tracer:
                return
            current_ctx = _ot.set_span_in_context(_ot.get_current_span())
            span = tracer.start_span(
                f"gen_ai.chat {self.model}",
                context=current_ctx,
                start_time=span_start_ns,
            )
            set_span_attributes(span, {
                "gen_ai.operation.name": "chat",
                "gen_ai.system": "vertex_ai",
                "gen_ai.request.model": self.model,
                "gen_ai.request.max_tokens": max_tokens,
                "gen_ai.usage.input_tokens": usage["input_tokens"],
                "gen_ai.usage.output_tokens": usage["billable_output_tokens"],
            })
            span.end(end_time=span_end_ns)
        except Exception:
            pass

    def _call_model(self, prompt: str, max_tokens: int):
        """One logical call to Gemini, with the existing 429 retry loop.

        Returns (response, usage) -- the RAW response object, not just its text --
        so callers that need more than the text (llm_json(), below, needs
        response.candidates[0].finish_reason to tell a truncated response apart
        from a genuinely malformed one) don't have to re-implement this retry
        loop. llm() and llm_json() both go through this single call site now;
        behavior for llm() is unchanged (same retry loop, same blocked-response
        check, same usage accounting), just relocated.
        """
        client = self._get_client()

        for attempt in range(3):
            try:
                span_start_ns = int(time.time() * 1e9)
                response = client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        max_output_tokens=max_tokens,
                        temperature=0.0,
                        thinking_config=types.ThinkingConfig(thinking_budget=self.thinking_budget),
                    ),
                )
                span_end_ns = int(time.time() * 1e9)
                duration_s = round((span_end_ns - span_start_ns) / 1e9, 3)

                usage = self._extract_usage(response, duration_s)

                session = self._session()
                session["input"] += usage["input_tokens"]
                session["cached_input"] += usage["cached_input_tokens"]
                session["output"] += usage["output_tokens"]
                session["reasoning"] += usage["reasoning_tokens"]
                session["tool"] += usage["tool_tokens"]
                session["total"] += usage["total_tokens"]
                session["calls"] += 1
                session["duration_s"] += duration_s

                log.debug(
                    "gemini call input=%d cached_input=%d output=%d reasoning=%d "
                    "tool=%d total=%d cost=$%.6f finish_reason=%s",
                    usage["input_tokens"], usage["cached_input_tokens"], usage["output_tokens"],
                    usage["reasoning_tokens"], usage["tool_tokens"], usage["total_tokens"],
                    usage["cost_usd"], _finish_reason_name(response),
                )

                self._emit_gen_ai_span(usage, max_tokens, span_start_ns, span_end_ns)

                # response.text is None when the model returned no text part at
                # all. Real cause seen in production 2026-08-26: Model Armor's
                # AI_PLATFORM floor setting was running inspect_and_block=true and
                # its pi_and_jailbreak filter matched the agent's OWN static
                # system prompt ("You are an SRE evidence analyst..."). The call
                # was blocked, Gemini returned no text, and this line raised
                # "'NoneType' object has no attribute 'strip'" -- an opaque
                # AttributeError that says nothing about what actually happened.
                # Fail with a message that names the likely cause instead.
                if response.text is None:
                    raise RuntimeError(
                        "LLM returned no text. The call was most likely blocked before "
                        "the model could answer -- check "
                        "modelarmor.googleapis.com/sanitize_operations for a "
                        "MATCH_FOUND entry at this timestamp. Raising instead of "
                        "returning an empty string, so no downstream node can mistake "
                        "a blocked call for a real answer."
                    )
                return response, usage

            except Exception as e:
                # Section 9 (2026-09-08): 5xx used to get ZERO retries at all -- any
                # transient server error (500/503/504) failed the whole investigation
                # on the first occurrence, identical treatment to a genuinely fatal
                # error. 429 already retried; extending the SAME bounded, capped
                # backoff to 5xx closes that gap without introducing a second,
                # different retry policy. Still never unbounded: same 3-attempt
                # ceiling, same linear 30s/60s wait, same hard raise after.
                retryable = any(code in str(e) for code in ("429", "500", "503", "504"))
                if retryable and attempt < 2:
                    wait = 30 * (attempt + 1)
                    log.warning(
                        "Transient LLM error (%s) — waiting %ds before retry %d/3",
                        str(e)[:200], wait, attempt + 1,
                    )
                    time.sleep(wait)
                else:
                    raise

        raise RuntimeError("Max retries exceeded")

    def llm(self, system: str, user: str, *, max_tokens: int = 1024) -> tuple[str, LLMUsage]:
        response, usage = self._call_model(f"{system}\n\n{user}", max_tokens)
        return response.text.strip(), usage

    def _extract_usage(self, response, duration_s: float) -> LLMUsage:
        """Maps Gemini's raw usage_metadata onto the normalized LLMUsage schema.

        Captures every field usage_metadata actually offers (issue #63 PR 1 —
        previously only prompt_token_count/candidates_token_count were read, so
        thoughts_token_count silently went uncounted and unbilled everywhere).
        Uses the API's own total_token_count directly rather than recomputing
        it locally (issue #63's root cause for the 2,548-token discrepancy was
        exactly that kind of local recomputation losing fidelity).
        """
        input_tokens = 0
        cached_input_tokens = 0
        output_tokens = 0
        reasoning_tokens = 0
        tool_tokens = 0
        total_tokens = 0

        if hasattr(response, "usage_metadata") and response.usage_metadata:
            meta = response.usage_metadata
            input_tokens = getattr(meta, "prompt_token_count", 0) or 0
            cached_input_tokens = getattr(meta, "cached_content_token_count", 0) or 0
            output_tokens = getattr(meta, "candidates_token_count", 0) or 0
            reasoning_tokens = getattr(meta, "thoughts_token_count", 0) or 0
            tool_tokens = getattr(meta, "tool_use_prompt_token_count", 0) or 0
            total_tokens = getattr(meta, "total_token_count", 0) or 0

        billable_output_tokens = output_tokens + reasoning_tokens
        cost_usd = self._calculate_cost(billable_output_tokens, input_tokens)

        return LLMUsage(
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            tool_tokens=tool_tokens,
            total_tokens=total_tokens,
            billable_output_tokens=billable_output_tokens,
            cost_usd=cost_usd,
            provider="gemini",
            model=self.model,
            duration_s=duration_s,
        )

    @staticmethod
    def _extract_json_object(text: str):
        """Best-effort JSON-object extraction from a model response.

        Returns (parsed_dict_or_None, failure_reason_or_""). Never raises --
        every failure path returns a short, content-free reason string instead
        (issue #76: never log/carry the model's actual raw text on a failure
        path, only length/position metadata).
        """
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
        text = text.strip()

        try:
            return json.loads(text), ""
        except json.JSONDecodeError:
            pass

        start = text.find("{")
        if start == -1:
            return None, f"no JSON object in model response (length={len(text)})"

        depth, end = 0, start
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        candidate = text[start:end]
        candidate = re.sub(r",\s*}", "}", candidate)
        candidate = re.sub(r",\s*]", "]", candidate)

        try:
            return json.loads(candidate), ""
        except json.JSONDecodeError as exc:
            return None, (
                f"malformed JSON from model (candidate length={len(candidate)}, "
                f"error at pos {getattr(exc, 'pos', -1)}): {exc.msg}"
            )

    def _build_json_request(self, system: str, user: str) -> str:
        """The EXACT text llm_json() sends to the model for a given (system, user) pair
        -- factored out so count_json_request_tokens() counts precisely this, never a
        caller-side approximation that could drift from it (2026-09-02 correction: a
        verifier-side `f"{system}\\n\\n{user}"` undercounted this response-format
        instruction). This is the one place Gemini's real request shape is
        constructed; agent.confidence.verifier never builds this itself.
        """
        system_json = system + "\n\nRespond ONLY with valid JSON. No markdown fences, no preamble."
        return f"{system_json}\n\n{user}"

    def count_json_request_tokens(self, system: str, user: str) -> int:
        return self.count_tokens(self._build_json_request(system, user))

    def llm_json(self, system: str, user: str, *, max_tokens: int = 1024) -> tuple[dict, LLMUsage]:
        """Calls Gemini and parses the response as JSON.

        2026-08-31: previously called self.llm() and only ever saw the extracted
        text, never the raw response -- so a response CUT OFF by max_output_tokens
        (finish_reason=MAX_TOKENS) and a genuinely malformed one were indistinguishable.
        Neither json.loads() nor the brace-repair pass in _extract_json_object() CAN
        recover a truncated response (an unterminated string or unbalanced braces is
        missing information, not a formatting quirk) -- so every truncation was
        guaranteed to surface as an opaque "malformed JSON from model" failure with no
        way to tell it apart from a real model-output bug. Root-caused against
        cascading-001/pending-001/mcp-gateway-failure-001's intermittent
        "model's response could not be parsed" failures (docs/management/
        confidence-genericity-review-2026-08-28.md): only the higher-complexity,
        multi-claim cases ever hit this, never the simple single-cause ones --
        consistent with an output-length problem, not a random glitch.
        """
        prompt = self._build_json_request(system, user)
        response, usage = self._call_model(prompt, max_tokens)

        parsed, failure_reason = self._extract_json_object(response.text.strip())

        if parsed is None and _is_truncated(response) and max_tokens < _TRUNCATION_RETRY_MAX_TOKENS:
            # Root cause is now POSITIVELY IDENTIFIED (the provider's own
            # finish_reason, not an inference from the malformed text) -- this is a
            # targeted retry tied to a diagnosed condition, not a blind
            # retry-on-any-failure. One attempt only, with a materially larger
            # budget (never just +1 token), capped by _TRUNCATION_RETRY_MAX_TOKENS
            # so a pathological prompt can't runaway the cost.
            retry_max_tokens = min(max_tokens * 2, _TRUNCATION_RETRY_MAX_TOKENS)
            log.warning(
                "llm_json: response truncated (finish_reason=MAX_TOKENS) at "
                "max_tokens=%d before a JSON object could be parsed -- retrying once "
                "with max_tokens=%d",
                max_tokens, retry_max_tokens,
            )
            retry_response, retry_usage = self._call_model(prompt, retry_max_tokens)
            retry_parsed, retry_failure_reason = self._extract_json_object(retry_response.text.strip())

            # Both calls' costs/tokens are real spend for this one llm_json() call --
            # summed field-by-field (never re-derived) so the caller's cost/token
            # accounting reflects both attempts, not just whichever ran last.
            usage = LLMUsage(
                input_tokens=usage["input_tokens"] + retry_usage["input_tokens"],
                cached_input_tokens=usage["cached_input_tokens"] + retry_usage["cached_input_tokens"],
                output_tokens=usage["output_tokens"] + retry_usage["output_tokens"],
                reasoning_tokens=usage["reasoning_tokens"] + retry_usage["reasoning_tokens"],
                tool_tokens=usage["tool_tokens"] + retry_usage["tool_tokens"],
                total_tokens=usage["total_tokens"] + retry_usage["total_tokens"],
                billable_output_tokens=usage["billable_output_tokens"] + retry_usage["billable_output_tokens"],
                cost_usd=round(usage["cost_usd"] + retry_usage["cost_usd"], 6),
                provider=retry_usage["provider"],
                model=retry_usage["model"],
                duration_s=usage["duration_s"] + retry_usage["duration_s"],
            )

            if retry_parsed is not None:
                log.info("llm_json: truncation retry succeeded (max_tokens=%d)", retry_max_tokens)
                return retry_parsed, usage

            response, parsed, failure_reason = retry_response, retry_parsed, (
                f"{retry_failure_reason} (after a truncation retry at max_tokens={retry_max_tokens} "
                f"-- still truncated: {_is_truncated(retry_response)})"
            )

        if parsed is not None:
            return parsed, usage

        # issue #76: log only length/position metadata, never the model's raw text --
        # same content-capture concern as the Trace flag elsewhere in this file.
        # 2026-08-27: this used to return a bare {}, which no caller could tell apart
        # from a legitimately empty result -- the failure was completely silent.
        # Local import beside its use, same reason as elsewhere in this file: the
        # repo's auto-formatter strips a top-level import whose usage lands later.
        from agent.llm.base import LLM_JSON_PARSE_FAILED_KEY
        truncated = _is_truncated(response)
        log.error(
            "llm_json: %s -- truncated=%s -- returning a marked-failed result so "
            "callers can detect this",
            failure_reason, truncated,
        )
        reason = failure_reason + (" (truncated: finish_reason=MAX_TOKENS)" if truncated else "")
        return {LLM_JSON_PARSE_FAILED_KEY: reason}, usage

    def count_tokens(self, text: str) -> int:
        """Real token count via the installed google-genai SDK's own CountTokens
        capability (Client.models.count_tokens) -- confirmed against the actually
        installed SDK version, not assumed. See
        https://ai.google.dev/gemini-api/docs/tokens: "Make this call before sending
        input to check the size of your requests."
        """
        response = self._get_client().models.count_tokens(model=self.model, contents=text)
        return response.total_tokens

    def max_context_tokens(self) -> int:
        """This model's input-token limit. Tries the provider's own live model
        metadata FIRST (Client.models.get(...).input_token_limit) -- if Google ever
        populates this for Vertex-hosted models, this starts working with zero code
        change here. Falls back to _VERTEX_INPUT_TOKEN_LIMIT_FALLBACK only because that
        live field is confirmed (2026-09-02, both via google-genai 1.47.0/2.10.0's
        Client.models.get() and the aiplatform_v1beta1 ModelGardenService's
        get_publisher_model()) to always return None for Gemini models on the Vertex AI
        backend (vertexai=True, what this adapter always uses) -- unlike the separate,
        non-Vertex Gemini Developer API, which does populate it. Raises for a model not
        in the fallback table rather than guessing; callers (verify_primary_claim) treat
        that as a fail-closed context-sizing failure, never as "fits". Cached after the
        first resolution since this is a static model property, not per-call usage.
        """
        if self._max_context_tokens is not None:
            return self._max_context_tokens

        model_info = self._get_client().models.get(model=self.model)
        if model_info.input_token_limit:
            self._max_context_tokens = model_info.input_token_limit
            return self._max_context_tokens

        limit = _VERTEX_INPUT_TOKEN_LIMIT_FALLBACK.get(self.model)
        if limit is None:
            raise RuntimeError(
                f"max_context_tokens: Vertex AI's model metadata does not report "
                f"input_token_limit for {self.model!r}, and no documented fallback is "
                f"registered for this model in _VERTEX_INPUT_TOKEN_LIMIT_FALLBACK -- "
                f"add one from https://ai.google.dev/gemini-api/docs/models rather "
                f"than guessing."
            )
        self._max_context_tokens = limit
        return self._max_context_tokens

    def get_session_usage(self) -> dict:
        session = self._session()
        return {
            "session_tokens_input": session["input"],
            "session_tokens_cached_input": session["cached_input"],
            "session_tokens_output": session["output"],
            "session_tokens_reasoning": session["reasoning"],
            "session_tokens_tool": session["tool"],
            "session_tokens_total": session["total"],
            "session_calls": session["calls"],
            "session_cost_usd": self._calculate_cost(
                session["output"] + session["reasoning"], session["input"]
            ),
            "session_model_latency_s": round(session["duration_s"], 3),
        }
