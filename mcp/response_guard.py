"""
response_guard.py — central FastMCP response interceptor calling Model Armor
directly (application-level sanitization) before any tool result reaches the
Streamable HTTP transport.

Why this exists (2026-09-06, follow-on to #203): Agent Gateway's CONTENT_AUTHZ
extension never inspects MCP tools/call RESPONSE bodies (RESPONSE_BODY never
appears as a serviceExtensionInfo event on real traffic, confirmed twice —
2026-09-05 and again 2026-09-06 after trying a json_response=True fix that
made no difference — and matches Google's own docs, which list "Streamable
HTTP/SSE for MCP" as excluded from gateway sanitization; see
PHASE1_EVIDENCE_LOG.md). Google's docs (docs.cloud.google.com/model-armor/
integrations) confirm calling Model Armor's REST/SDK API directly from
application code is a real, supported pattern — but "detector only": the
caller must act on the verdict itself. The gateway is what adds automatic
enforcement; this file is what does that instead, at the one place all tool
responses already pass through.

This is ONE middleware, registered ONCE (mcp.add_middleware in server.py),
wrapping every tools/call response via FastMCP's on_call_tool hook — no
per-tool code. It mirrors the same call shape agent/main.py's _sanitize()
already uses in production for the agent's own query text / final RCA summary
(SanitizeModelResponseRequest, FilterMatchState.MATCH_FOUND) — that call
sanitizes the bookends only (user query in, final text out), never the raw
tool responses flowing through the middle of an investigation. This file
closes that specific gap on the MCP side.
"""
from __future__ import annotations

import json
import logging
import os

import mcp_types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult

log = logging.getLogger("sre-mcp.response_guard")

REGION = os.environ.get("REGION", "us-central1")
MODEL_ARMOR_RESPONSE_TEMPLATE = os.environ.get("MODEL_ARMOR_RESPONSE_TEMPLATE", "")


# Section 8 (2026-09-08): kept in sync with agent/nodes/evidence_extractor.py's own
# copy of this exact string by comment, not by shared import -- mcp/ and agent/ are
# separate deployable services/containers with no shared Python import path in
# production, the same reason the two already-existing truncation-marker strings in
# this codebase are each their own local literal rather than a shared constant.
UNINSPECTED_MARKER = (
    "[UNINSPECTED: Model Armor response check unavailable for this tool result — "
    "content was not verified]"
)


def _mark_uninspected(result: ToolResult) -> ToolResult:
    """Marks this result as NOT verified by Model Armor -- WITHOUT breaking how
    agent/mcp_client.py::_extract_content() actually reads a response.

    Verified against that exact function before choosing this approach: it checks
    result["structuredContent"] FIRST if present, else reads ONLY content[0] and
    json.loads()'s its text -- any block after index 0 is silently ignored, and
    prepending a non-JSON text block at index 0 would have replaced the real content
    with the marker string entirely (a severe, easy-to-miss regression). Every tool
    in this server returns a JSON-serializable dict (mcp/security.py's
    _postprocess), so injecting a real "_uninspected": true KEY into that same
    dict -- in both structured_content and content[0], whichever the caller reads --
    survives round-trip and is detected by evidence_extractor.py without altering
    the actual evidence content at all.
    """
    marked_structured = result.structured_content
    if isinstance(marked_structured, dict):
        marked_structured = {**marked_structured, "_uninspected": True}

    marked_content = list(result.content) if result.content else []
    if marked_content:
        first = marked_content[0]
        text = getattr(first, "text", None)
        if text:
            try:
                payload = json.loads(text)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                payload["_uninspected"] = True
                marked_content[0] = mt.TextContent(type="text", text=json.dumps(payload))
            else:
                # Not JSON-shaped (should not happen for this server's tools) --
                # fall back to a separate marker block. A content[0]-only reader
                # ignores it (no worse than doing nothing); never overwrites real
                # content.
                marked_content.append(mt.TextContent(type="text", text=UNINSPECTED_MARKER))

    return ToolResult(
        content=marked_content or result.content,
        structured_content=marked_structured,
        is_error=result.is_error,
    )


def _extract_text(result: ToolResult) -> str:
    """Join every text content block into one string for sanitization.

    Every tool in this server returns a JSON-serializable dict (see
    security.py's _postprocess) which FastMCP renders as text content —
    non-text blocks are skipped rather than guessed at, since none of our
    tools produce any.
    """
    parts = []
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _tool_name(context: MiddlewareContext) -> str:
    return getattr(context.message, "name", "?")


class ModelArmorResponseGuard(Middleware):
    """Sanitizes every tool response through Model Armor's response-guard
    template before it reaches the MCP transport. See module docstring."""

    def __init__(self):
        self._client = None
        if not MODEL_ARMOR_RESPONSE_TEMPLATE:
            log.info("MODEL_ARMOR_RESPONSE_TEMPLATE not set — response sanitization disabled")
            return
        try:
            from google.api_core.client_options import ClientOptions
            from google.cloud import modelarmor_v1

            self._client = modelarmor_v1.ModelArmorClient(
                client_options=ClientOptions(
                    api_endpoint=f"modelarmor.{REGION}.rep.googleapis.com"
                )
            )
            log.info("ModelArmorResponseGuard ready: %s", MODEL_ARMOR_RESPONSE_TEMPLATE)
        except Exception as exc:
            # Section 8 (2026-09-08): this used to be log.warning with no alertable
            # signal -- a configured guard that fails to INITIALIZE degraded to
            # permanently-uninspected (self._client stays None for the process
            # lifetime) with only a one-time WARNING log, no metric, no alert. The
            # existing mcp_model_armor_fail_open alert (iac/agent/monitoring.tf)
            # only fires on the per-call sanitize_model_response exception below,
            # never on init failure -- a distinct log-based metric for this exact
            # string is added in the same Terraform change as this fix.
            log.error(
                "model_armor_guard_init_failed event=init_error error=%s — "
                "response sanitization PERMANENTLY disabled for this process/revision",
                exc,
            )

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        result = await call_next(context)

        if not self._client:
            # Not configured — behave exactly as if this middleware were absent.
            return result

        text = _extract_text(result)
        if not text:
            return result

        try:
            from google.cloud import modelarmor_v1

            resp = self._client.sanitize_model_response(
                modelarmor_v1.SanitizeModelResponseRequest(
                    name=MODEL_ARMOR_RESPONSE_TEMPLATE,
                    model_response_data=modelarmor_v1.DataItem(text=text),
                )
            )
            blocked = (
                resp.sanitization_result.filter_match_state
                == modelarmor_v1.FilterMatchState.MATCH_FOUND
            )
        except Exception as exc:
            # Model Armor itself unavailable/erroring on a RESPONSE check —
            # FAIL OPEN, deliberately asymmetric with a request-side failure
            # (which would fail closed), matching the existing, already-
            # decided precedent in agent/main.py::_sanitize(is_output=True):
            # withholding every read-only investigation tool result during a
            # transient Model Armor outage would break the primary GKE/
            # on-prem RCA path over a safety-filter dependency that is not
            # itself part of the investigation. Logged at ERROR so a
            # log-based alert can fire on fail-open events, same as the
            # existing model_armor_fail_open line.
            log.error(
                "model_armor_fail_open event=sanitize_error tool=%s error=%s",
                _tool_name(context), exc,
            )
            # Section 8 (2026-09-08): "not inspected" used to be invisible past this
            # point -- the original result was returned completely unmodified, so
            # evidence_extractor/rca_builder/memory-write had no way to know this
            # specific tool response bypassed Model Armor. Fixed the SAME way this
            # codebase already marks truncation (evidence_extractor.py's "...[middle
            # truncated...]..." convention) -- a machine-parseable text marker that
            # survives through the exact same content path every downstream reader
            # already processes, rather than inventing a new out-of-band channel
            # through the MCP wire protocol.
            return _mark_uninspected(result)

        if blocked:
            log.warning(
                "ModelArmorResponseGuard BLOCKED tool response tool=%s",
                _tool_name(context),
            )
            return ToolResult(
                content=[
                    mt.TextContent(
                        type="text",
                        text=(
                            "Tool response withheld: flagged by Model Armor "
                            "response safety filter."
                        ),
                    )
                ],
                is_error=True,
            )

        return result
