"""
Gemini 2.5 Flash client via Vertex AI.
- Real token tracking from usage_metadata
- Cost estimation per call and cumulative
- Thinking budget disabled for deterministic JSON responses
- Retry on 429 rate limit
"""
import os
import json
import logging
import re
import time

from google import genai
from google.genai import types

log = logging.getLogger("sre-agent.gemini")

PROJECT_ID = os.environ.get("PROJECT_ID", "your-gcp-project-id")
REGION     = os.environ.get("REGION", "us-central1")
MODEL      = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Vertex AI model-endpoint location. Reads GOOGLE_CLOUD_LOCATION (the exact env
# var the codelab agent sets, deploy_agent.py), defaulting to "global" to match
# the codelab. A regional location sends model traffic to
# us-central1-aiplatform[.mtls].googleapis.com, which the Agent Gateway's TLS
# inspection treats as a separate target; global is the codelab's proven path.
MODEL_ENDPOINT_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")

# Gemini 2.5 Flash pricing (on-demand)
# Verify at: https://cloud.google.com/vertex-ai/generative-ai/pricing
PRICE_INPUT_PER_1M  = float(os.environ.get("GEMINI_PRICE_INPUT",  "0.15"))
PRICE_OUTPUT_PER_1M = float(os.environ.get("GEMINI_PRICE_OUTPUT", "0.60"))

_client = None

# Session totals — cumulative for this process
_session_tokens_input  = 0
_session_tokens_output = 0
_session_calls         = 0


def _get_client():
    global _client
    if _client is None:
        # Pin the PLAIN (non-mTLS) Vertex endpoint. Under the Agent Gateway, the
        # SDK's default mTLS endpoint (aiplatform.mtls.googleapis.com — selected
        # because Agent Identity presents client certs) fails the gateway's TLS
        # inspection (CERTIFICATE_VERIFY_FAILED); GOOGLE_API_USE_MTLS_ENDPOINT is
        # ignored by google-genai. Setting http_options.base_url forces the plain
        # host, which the gateway inspects fine (like the MCP path).
        base_url = (
            "https://aiplatform.googleapis.com"
            if MODEL_ENDPOINT_LOCATION == "global"
            else f"https://{MODEL_ENDPOINT_LOCATION}-aiplatform.googleapis.com"
        )
        _client = genai.Client(
            vertexai=True,
            project=PROJECT_ID,
            location=MODEL_ENDPOINT_LOCATION,
            http_options=types.HttpOptions(base_url=base_url),
        )
        log.info("Gemini client initialized via Vertex AI: %s in %s/%s (endpoint %s)",
                 MODEL, PROJECT_ID, MODEL_ENDPOINT_LOCATION, base_url)
    return _client


def _calculate_cost(tokens_input: int, tokens_output: int) -> float:
    """Calculate estimated cost in USD from token counts."""
    return round(
        (tokens_input  / 1_000_000) * PRICE_INPUT_PER_1M +
        (tokens_output / 1_000_000) * PRICE_OUTPUT_PER_1M,
        6,
    )


def _emit_gen_ai_span(
    tokens_input: int,
    tokens_output: int,
    max_tokens: int,
    span_start_ns: int,
    span_end_ns: int,
) -> None:
    """Emit a gen_ai.* OTEL span so Agent Platform Models/Usage tabs show data.

    Attribute names follow OpenTelemetry Gen AI semantic conventions.
    Called after a successful Gemini API response so only completed calls are recorded.
    """
    try:
        from agent.otel import get_tracer, set_span_attributes
        from opentelemetry import trace as _ot
        tracer = get_tracer()
        if not tracer:
            return
        # Parent context must be set explicitly — start_span() without context
        # creates a root span, which Agent Platform cannot correlate to the trace.
        current_ctx = _ot.set_span_in_context(_ot.get_current_span())
        span = tracer.start_span(
            f"gen_ai.chat {MODEL}",
            context=current_ctx,
            start_time=span_start_ns,
        )
        set_span_attributes(span, {
            "gen_ai.operation.name":     "chat",
            "gen_ai.system":             "vertex_ai",
            "gen_ai.request.model":      MODEL,
            "gen_ai.request.max_tokens": max_tokens,
            "gen_ai.usage.input_tokens":  tokens_input,
            "gen_ai.usage.output_tokens": tokens_output,
        })
        span.end(end_time=span_end_ns)
    except Exception:
        pass


def llm(system: str, user: str, *, max_tokens: int = 1024) -> tuple[str, dict]:
    """
    Call Gemini and return (text, usage_metadata).
    usage_metadata: { tokens_input, tokens_output, tokens_total, cost_usd, model }
    """
    global _session_tokens_input, _session_tokens_output, _session_calls

    client = _get_client()
    prompt = f"{system}\n\n{user}"

    for attempt in range(3):
        try:
            _span_start_ns = int(time.time() * 1e9)
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.0,
                    # Disable thinking for deterministic SRE tool calls
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            _span_end_ns = int(time.time() * 1e9)

            # Extract real token counts from Gemini response metadata
            tokens_input  = 0
            tokens_output = 0
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                meta = response.usage_metadata
                tokens_input  = getattr(meta, "prompt_token_count",     0) or 0
                tokens_output = getattr(meta, "candidates_token_count", 0) or 0

            tokens_total = tokens_input + tokens_output
            cost_usd     = _calculate_cost(tokens_input, tokens_output)

            # Update session totals
            _session_tokens_input  += tokens_input
            _session_tokens_output += tokens_output
            _session_calls         += 1

            usage = {
                "tokens_input":  tokens_input,
                "tokens_output": tokens_output,
                "tokens_total":  tokens_total,
                "cost_usd":      cost_usd,
                "model":         MODEL,
            }

            log.debug(
                "gemini call tokens_in=%d tokens_out=%d cost=$%.6f",
                tokens_input, tokens_output, cost_usd,
            )

            _emit_gen_ai_span(tokens_input, tokens_output, max_tokens, _span_start_ns, _span_end_ns)

            return response.text.strip(), usage

        except Exception as e:
            if "429" in str(e) and attempt < 2:
                wait = 30 * (attempt + 1)
                log.warning("Rate limited — waiting %ds before retry %d/3", wait, attempt + 1)
                time.sleep(wait)
            else:
                raise

    raise RuntimeError("Max retries exceeded")


def llm_json(system: str, user: str, *, max_tokens: int = 1024) -> tuple[dict, dict]:
    """
    Call Gemini and parse JSON response.
    Returns (parsed_dict, usage_metadata).
    """
    text, usage = llm(
        system + "\n\nRespond ONLY with valid JSON. No markdown fences, no preamble.",
        user,
        max_tokens=max_tokens,
    )

    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```\s*$",        "", text, flags=re.MULTILINE)
    text = text.strip()

    # Direct parse
    try:
        return json.loads(text), usage
    except json.JSONDecodeError:
        pass

    # Find outermost { }
    start = text.find("{")
    if start == -1:
        log.warning("llm_json: no JSON object found: %s", text[:200])
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
    except json.JSONDecodeError:
        log.warning("llm_json: repair failed: %s", candidate[:300])
        return {}, usage


def get_session_usage() -> dict:
    """Return cumulative token usage for this process session."""
    return {
        "session_tokens_input":  _session_tokens_input,
        "session_tokens_output": _session_tokens_output,
        "session_tokens_total":  _session_tokens_input + _session_tokens_output,
        "session_calls":         _session_calls,
        "session_cost_usd":      _calculate_cost(_session_tokens_input, _session_tokens_output),
    }
