"""Regression test for the new per-MCP-call trace span added in tool_executor.py
(observability review, docs/management/observability-architecture-review-2026-08-23.md
section 6): before this, only the enclosing @trace_node("langgraph.tool_executor")
span existed -- no dedicated span per MCP call, so Cloud Trace couldn't show
individual MCP call latency/status in the waterfall view.

Uses a real OpenTelemetry TracerProvider backed by InMemorySpanExporter (no
network, no GCP credentials needed), same pattern as
tests/test_otel_span_content_safety.py.
"""
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent.otel as otel_mod
import agent.nodes.tool_executor as tool_executor_mod


@pytest.fixture
def in_memory_tracer(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    # tool_executor.py does `from agent.otel import get_tracer` as a LOCAL import
    # inside the function body -- re-executed every call, so patching
    # agent.otel.get_tracer itself (not tool_executor_mod's namespace) is what
    # that local import picks up. Same pattern as test_otel_span_content_safety.py.
    monkeypatch.setattr(otel_mod, "get_tracer", lambda: tracer)
    return tracer, exporter


def _state_with_action(tool: str, mcp_source: str) -> dict:
    return {
        "run_id": "run_test",
        "current_action": {"tool": tool, "mcp_source": mcp_source, "arguments": {}},
        "resolved_context": {"cluster_name": "sre-test-cluster"},
        "investigation": {"current_step": 1},
    }


def test_mcp_span_created_with_correct_attributes_on_success(monkeypatch, in_memory_tracer):
    tracer, exporter = in_memory_tracer
    monkeypatch.setattr(
        tool_executor_mod, "call_tool",
        lambda **kwargs: {
            "ok": True, "result": {"data": "x"}, "duration_s": 0.25,
            "tool": "describe_k8s_resource", "mcp_source": "gke_remote_mcp",
        },
    )
    state = _state_with_action(tool="describe_k8s_resource", mcp_source="gke_remote_mcp")
    tool_executor_mod.tool_executor(state)

    spans = exporter.get_finished_spans()
    mcp_spans = [s for s in spans if s.name == "mcp.describe_k8s_resource"]
    assert len(mcp_spans) == 1
    attrs = mcp_spans[0].attributes
    assert attrs["mcp.tool"] == "describe_k8s_resource"
    assert attrs["mcp.server"] == "gke_remote_mcp"
    assert attrs["tool.duration_ms"] == 250
    assert attrs["tool.status"] == "ok"


def test_mcp_span_records_error_status_on_failure(monkeypatch, in_memory_tracer):
    tracer, exporter = in_memory_tracer
    monkeypatch.setattr(
        tool_executor_mod, "call_tool",
        lambda **kwargs: {
            "ok": False, "error": "boom", "duration_s": 0.1,
            "tool": "describe_k8s_resource", "mcp_source": "gke_remote_mcp",
        },
    )
    state = _state_with_action(tool="describe_k8s_resource", mcp_source="gke_remote_mcp")
    tool_executor_mod.tool_executor(state)

    spans = exporter.get_finished_spans()
    mcp_spans = [s for s in spans if s.name == "mcp.describe_k8s_resource"]
    assert len(mcp_spans) == 1
    assert mcp_spans[0].attributes["tool.status"] == "error"


def test_tool_executor_still_works_when_tracer_is_none(monkeypatch):
    """get_tracer() returning None (tracing disabled/unavailable) must not break
    the actual tool call -- the nullcontext() fallback must be a no-op."""
    monkeypatch.setattr(otel_mod, "get_tracer", lambda: None)
    monkeypatch.setattr(
        tool_executor_mod, "call_tool",
        lambda **kwargs: {
            "ok": True, "result": {"data": "x"}, "duration_s": 0.1,
            "tool": "describe_k8s_resource", "mcp_source": "gke_remote_mcp",
        },
    )
    state = _state_with_action(tool="describe_k8s_resource", mcp_source="gke_remote_mcp")
    update = tool_executor_mod.tool_executor(state)

    assert update["tool_history"][0]["ok"] is True
