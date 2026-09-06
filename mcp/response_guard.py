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

import logging
import os

import mcp_types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult

log = logging.getLogger("sre-mcp.response_guard")

REGION = os.environ.get("REGION", "us-central1")
MODEL_ARMOR_RESPONSE_TEMPLATE = os.environ.get("MODEL_ARMOR_RESPONSE_TEMPLATE", "")


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
            log.warning(
                "ModelArmorResponseGuard init failed (%s) — response sanitization disabled",
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
            return result

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
