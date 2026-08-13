"""
agent/llm/gemini_adapter.py — the only place that imports the Gemini/Vertex AI SDK.

Everything Gemini-specific lives here: the google-genai client, thinking-budget
handling, usage_metadata field extraction, and (for now, per issue #63 PR 1 —
dynamic pricing is PR 2) the existing Terraform-sourced static pricing.
"""
from __future__ import annotations

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


class GeminiAdapter(LLMClient):
    """Vertex AI Gemini adapter. Model is fixed per instance (one adapter = one
    resolved LLM_PROFILE for the process lifetime — matches Agent Engine's
    single-model-per-deployment reality; nothing here switches models mid-run).
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
        self.reset_session()

    def reset_session(self) -> None:
        # issue #74: this instance is cached process-wide (agent.llm.registry) and
        # reused across every investigation a warm process handles -- these counters
        # used to accumulate forever (module-level globals before the LLM-adapter
        # refactor, now instance attributes on the same shared cached instance --
        # same underlying bug, different mechanism). agent/main.py's investigate()
        # calls this once at the start of every investigation so get_session_usage()
        # means "this investigation," not "everything since process start."
        self._session_input = 0
        self._session_cached_input = 0
        self._session_output = 0
        self._session_reasoning = 0
        self._session_tool = 0
        self._session_total = 0
        self._session_calls = 0
        self._session_duration_s = 0.0

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

    def llm(self, system: str, user: str, *, max_tokens: int = 1024) -> tuple[str, LLMUsage]:
        client = self._get_client()
        prompt = f"{system}\n\n{user}"

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

                self._session_input += usage["input_tokens"]
                self._session_cached_input += usage["cached_input_tokens"]
                self._session_output += usage["output_tokens"]
                self._session_reasoning += usage["reasoning_tokens"]
                self._session_tool += usage["tool_tokens"]
                self._session_total += usage["total_tokens"]
                self._session_calls += 1
                self._session_duration_s += duration_s

                log.debug(
                    "gemini call input=%d cached_input=%d output=%d reasoning=%d "
                    "tool=%d total=%d cost=$%.6f",
                    usage["input_tokens"], usage["cached_input_tokens"], usage["output_tokens"],
                    usage["reasoning_tokens"], usage["tool_tokens"], usage["total_tokens"],
                    usage["cost_usd"],
                )

                self._emit_gen_ai_span(usage, max_tokens, span_start_ns, span_end_ns)

                return response.text.strip(), usage

            except Exception as e:
                if "429" in str(e) and attempt < 2:
                    wait = 30 * (attempt + 1)
                    log.warning("Rate limited — waiting %ds before retry %d/3", wait, attempt + 1)
                    time.sleep(wait)
                else:
                    raise

        raise RuntimeError("Max retries exceeded")

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

    def llm_json(self, system: str, user: str, *, max_tokens: int = 1024) -> tuple[dict, LLMUsage]:
        text, usage = self.llm(
            system + "\n\nRespond ONLY with valid JSON. No markdown fences, no preamble.",
            user,
            max_tokens=max_tokens,
        )

        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
        text = text.strip()

        try:
            return json.loads(text), usage
        except json.JSONDecodeError:
            pass

        start = text.find("{")
        if start == -1:
            # issue #76: was logging up to 200 chars of the model's raw response text
            # (built from real k8s evidence) into Cloud Logging on this failure path --
            # same content-capture concern as the Trace flag above, different system.
            # Length-only is still useful for diagnosing "empty response" vs "malformed
            # response" without capturing the actual content.
            log.warning("llm_json: no JSON object found (response length=%d)", len(text))
            return {}, usage

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
            return json.loads(candidate), usage
        except json.JSONDecodeError as exc:
            # issue #76: same fix as above -- length + parser error position, not the
            # actual candidate text.
            log.warning(
                "llm_json: repair failed (candidate length=%d, error at pos %d): %s",
                len(candidate), getattr(exc, "pos", -1), exc.msg,
            )
            return {}, usage

    def get_session_usage(self) -> dict:
        return {
            "session_tokens_input": self._session_input,
            "session_tokens_cached_input": self._session_cached_input,
            "session_tokens_output": self._session_output,
            "session_tokens_reasoning": self._session_reasoning,
            "session_tokens_tool": self._session_tool,
            "session_tokens_total": self._session_total,
            "session_calls": self._session_calls,
            "session_cost_usd": self._calculate_cost(
                self._session_output + self._session_reasoning, self._session_input
            ),
            "session_model_latency_s": round(self._session_duration_s, 3),
        }
