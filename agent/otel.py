"""
otel.py — OpenTelemetry helpers for the SRE Agent.

Purpose:
- Export LangGraph investigation spans to Google Cloud Trace.
- Never fail the agent if OpenTelemetry is unavailable or misconfigured.
- Keep node files small by using one decorator per LangGraph node.
"""
from __future__ import annotations

import functools
import json
import logging
import os
from typing import Any, Callable

log = logging.getLogger("sre-agent.otel")

_tracer = None
_provider = None
_otel_error_logged = False


def _safe_str(value: Any, limit: int = 256) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except Exception:
        return default


def get_tracer():
    """Return a Cloud Trace OpenTelemetry tracer, or None if unavailable."""
    global _tracer, _provider, _otel_error_logged

    if _tracer is not None:
        return _tracer

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

        project_id = os.environ.get("PROJECT_ID", "your-gcp-project-id")

        resource = Resource.create(
            {
                "service.name": os.environ.get("OTEL_SERVICE_NAME", "sre-agent-gcp"),
                "service.namespace": "agent-engine",
                "deployment.environment": os.environ.get("ENVIRONMENT", "demo"),
                "cloud.provider": "gcp",
                "cloud.platform": "gcp_vertex_ai_agent_engine",
                "gcp.project_id": project_id,
            }
        )

        provider = TracerProvider(resource=resource)
        processor = BatchSpanProcessor(CloudTraceSpanExporter(project_id=project_id))
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)

        # issue #130 (2nd follow-up): set_tracer_provider() can only succeed ONCE per
        # process and silently no-ops (logs its own warning, doesn't raise) on every
        # later call -- confirmed live that Agent Engine's own managed runtime also
        # calls it, during its own startup bootstrap, shortly after ours. Storing our
        # local `provider` in `_provider` and just assuming it's the one actually
        # backing spans would mean flush_traces() could be flushing an orphaned
        # provider while real spans go through whichever provider actually won the
        # race -- verify which one actually won instead of assuming.
        active_provider = trace.get_tracer_provider()
        provider_installed = active_provider is provider
        log.info(
            "otel provider check: provider_installed=%s active_provider_type=%s configured_provider_type=%s",
            provider_installed, type(active_provider).__name__, type(provider).__name__,
        )

        if provider_installed:
            _provider = provider
            _tracer = trace.get_tracer("sre-agent-gcp")
            log.info("OpenTelemetry Cloud Trace exporter initialized project=%s", project_id)
        elif hasattr(active_provider, "add_span_processor"):
            # Another provider won the race, but it's a real SDK TracerProvider (not a
            # no-op stub) -- attach our exporter to the one actually in effect instead
            # of flushing an orphan no one reads spans from.
            active_provider.add_span_processor(processor)
            _provider = active_provider
            _tracer = trace.get_tracer("sre-agent-gcp")
            log.info(
                "OpenTelemetry Cloud Trace exporter attached to existing active provider "
                "project=%s active_provider_type=%s",
                project_id, type(active_provider).__name__,
            )
        else:
            log.warning(
                "OpenTelemetry disabled: our TracerProvider was rejected and the "
                "active provider (%s) does not support add_span_processor -- refusing "
                "to flush an orphan provider no spans actually go through",
                type(active_provider).__name__,
            )
            return None

        return _tracer

    except Exception as exc:
        if not _otel_error_logged:
            log.warning("OpenTelemetry disabled or failed to initialize: %s", exc)
            _otel_error_logged = True
        return None


def get_trace_id_hex() -> str:
    """Return the current OTel span's trace_id as a 32-char lowercase hex string, or ""
    if there is no active span (tracer disabled/unavailable) or the span context is
    invalid. Used to correlate the per-run structured RCA log (Cloud Logging) with the
    Cloud Trace spans emitted by trace_node — see PRODUCTION-LAUNCH-PLAN.md Priority 10
    ("trace_id correlation").
    """
    try:
        from opentelemetry import trace as _ot

        span_context = _ot.get_current_span().get_span_context()
        if not span_context or not span_context.is_valid:
            return ""
        return format(span_context.trace_id, "032x")
    except Exception:
        return ""


def flush_traces(timeout_millis: int = 5000) -> None:
    """Flush spans before Agent Engine response returns."""
    try:
        if _provider is not None:
            # issue #130 diagnostic: force_flush()'s return value used to be discarded
            # entirely. It returns False (not an exception) on a timeout, which would
            # explain zero traces landing in Cloud Trace with zero errors anywhere in
            # the logs -- log the real result so that is confirmed or ruled out.
            flushed = _provider.force_flush(timeout_millis=timeout_millis)
            log.info("flush_traces result=%s timeout_millis=%d", flushed, timeout_millis)
    except Exception as exc:
        log.warning("OpenTelemetry force_flush failed: %s", exc)


def set_span_attributes(span: Any, attrs: dict[str, Any]) -> None:
    if not span:
        return
    for key, value in attrs.items():
        if value is None:
            continue
        try:
            if isinstance(value, (str, bool, int, float)):
                span.set_attribute(key, value)
            elif isinstance(value, (list, tuple)):
                span.set_attribute(key, ",".join(_safe_str(v, 80) for v in value[:20]))
            else:
                span.set_attribute(key, _safe_str(value))
        except Exception:
            # Attribute failures must not break incident investigation.
            pass


def _state_attrs(state: dict[str, Any], node_name: str) -> dict[str, Any]:
    inv = state.get("investigation", {}) or {}
    ctx = state.get("resolved_context", {}) or {}
    return {
        "sre.node": node_name,
        "sre.run_id": state.get("run_id", ""),
        "sre.incident_id": state.get("incident_id", ""),
        "sre.step": _safe_int(inv.get("current_step", 0)),
        "sre.status": inv.get("status", ""),
        "sre.confidence": _safe_float(inv.get("confidence", 0.0)),
        "sre.confidence_band": inv.get("confidence_band", ""),
        "sre.cluster": ctx.get("cluster_name", ctx.get("cluster", "")),
        "sre.region": ctx.get("cluster_region", ""),
        "sre.namespace": ctx.get("namespace", ""),
        "sre.pod": ctx.get("pod", ""),
        "sre.incident_type": ctx.get("incident_type", ""),
        "sre.evidence_count": len(state.get("evidence_ids", []) or []),
        "sre.tool_calls": len(state.get("tool_history", []) or []),
        "sre.tokens_total": _safe_int(inv.get("tokens_total", 0)),
        "sre.estimated_cost_usd": _safe_float(inv.get("estimated_cost_usd", 0.0)),
    }


def _result_attrs(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}

    inv = result.get("investigation", {}) or {}
    action = result.get("current_action", {}) or {}
    latest = result.get("latest_tool_result", {}) or {}

    attrs = {
        "sre.result.keys": list(result.keys()),
    }

    if inv:
        attrs.update(
            {
                "sre.result.status": inv.get("status", ""),
                "sre.result.confidence": _safe_float(inv.get("confidence", 0.0)),
                "sre.result.confidence_band": inv.get("confidence_band", ""),
                "sre.result.tokens_total": _safe_int(inv.get("tokens_total", 0)),
                "sre.result.estimated_cost_usd": _safe_float(inv.get("estimated_cost_usd", 0.0)),
                "sre.result.loop_exit_reason": inv.get("loop_exit_reason", ""),
            }
        )

    if action:
        attrs.update(
            {
                "sre.result.action_tool": action.get("tool", ""),
                "sre.result.action_source": action.get("mcp_source", ""),
            }
        )

    if latest:
        attrs.update(
            {
                "sre.result.latest_tool": latest.get("tool", ""),
                "sre.result.latest_source": latest.get("mcp_source", ""),
                "sre.result.latest_ok": bool(latest.get("ok", False)),
            }
        )

    if "evidence_ids" in result:
        attrs["sre.result.new_evidence_ids"] = result.get("evidence_ids", [])
        attrs["sre.result.new_evidence_count"] = len(result.get("evidence_ids", []) or [])

    if "errors" in result:
        attrs["sre.result.error_count"] = len(result.get("errors", []) or [])

    return attrs


def diag_161_log_context(label: str, **extra: Any) -> None:
    """TEMPORARY diagnostic for issue #161 (Cloud Trace span fragmentation during
    native stream_query()). Logs thread id + the CURRENT OTel trace/span id seen
    at this exact point -- metadata only, no prompts/evidence/exceptions. Remove
    once #161's root cause is confirmed and this diagnostic pass concludes.
    """
    import threading

    try:
        from opentelemetry import trace as _ot

        span_ctx = _ot.get_current_span().get_span_context()
        valid = bool(span_ctx and span_ctx.is_valid)
        trace_id = format(span_ctx.trace_id, "032x") if valid else "none"
        span_id = format(span_ctx.span_id, "016x") if valid else "none"
    except Exception:
        valid, trace_id, span_id = False, "error", "error"

    extra_str = " ".join(f"{k}={v}" for k, v in extra.items())
    log.info(
        "otel_diag_161 label=%s thread=%s trace_id=%s span_id=%s valid=%s %s",
        label, threading.get_ident(), trace_id, span_id, valid, extra_str,
    )


def trace_node(span_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator for LangGraph node functions."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(state: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            if tracer is None:
                return func(state, *args, **kwargs)

            # issue #161 diagnostic (D): parent context as trace_node sees it,
            # BEFORE start_as_current_span() creates the child. TEMPORARY.
            diag_161_log_context("D_trace_node_before_start", node=span_name)

            # issue #76 (compliance follow-up): record_exception=False/
            # set_status_on_exception=False -- otherwise OTel's own context-manager
            # __exit__ independently auto-records the exception's message when the
            # `raise` below re-propagates it, even with the explicit except block
            # already handling it safely.
            with tracer.start_as_current_span(
                span_name, record_exception=False, set_status_on_exception=False,
            ) as span:
                set_span_attributes(span, _state_attrs(state, span_name))
                try:
                    result = func(state, *args, **kwargs)
                    set_span_attributes(span, _result_attrs(result))
                    span.set_attribute("sre.node.success", True)
                    return result
                except Exception as exc:
                    # issue #76 (compliance follow-up): record_exception()/Status(...,
                    # str(exc))/sre.node.error used to send the exception's own message
                    # text into the span -- an exception can quote response content,
                    # operational data, or a Kubernetes error string, not just a bare
                    # error name. Only the exception's TYPE (a class name, e.g.
                    # "ValueError") is safe metadata; the message itself is not.
                    try:
                        from opentelemetry.trace import Status, StatusCode

                        span.set_status(Status(StatusCode.ERROR))
                    except Exception:
                        pass
                    span.set_attribute("sre.node.success", False)
                    span.set_attribute("sre.node.error_type", type(exc).__name__)
                    raise

        return wrapper

    return decorator


def log_node_tokens(node: str, run_id: str, step: int, usage: dict) -> None:
    """Emit a structured per-node token event to stdout.

    Cloud Logging parses stdout JSON lines as jsonPayload automatically.
    Use this to build log-based metrics and charts per node.

    `usage` is the per-call LLMUsage a node just got back from agent.llm's
    llm()/llm_json() (see agent/llm/base.py) -- normalized field names, not
    the accumulated investigation-level tokens_input/tokens_output/
    tokens_total. Reading the old field names here was a real bug (found
    2026-08-11 reviewing issue #63 PR 1): every per-node log line silently
    reported all zeros, since usage.get("tokens_input", 0) etc. never matched
    any key LLMUsage actually has. tokens_output below is
    billable_output_tokens (candidates + reasoning, what actually bills at
    the output rate) -- tokens_candidates/tokens_reasoning preserve the
    breakdown, same convention as agent/llm/accounting.py's accumulate_usage().
    """
    print(json.dumps({
        "event_type":         "node_token_usage",
        "node":               node,
        "run_id":              run_id,
        "step":                step,
        "tokens_input":        usage.get("input_tokens", 0),
        "tokens_cached_input": usage.get("cached_input_tokens", 0),
        "tokens_output":       usage.get("billable_output_tokens", 0),
        "tokens_candidates":   usage.get("output_tokens", 0),
        "tokens_reasoning":    usage.get("reasoning_tokens", 0),
        "tokens_tool_use":     usage.get("tool_tokens", 0),
        "tokens_total":        usage.get("total_tokens", 0),
        "cost_usd":            usage.get("cost_usd", 0.0),
    }, separators=(",", ":")), flush=True)
