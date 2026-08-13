"""Regression tests for issue #76's compliance follow-up: custom trace-span code (not
just the OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT flag) must never carry
investigation content -- queries, exception messages, prompts, responses, k8s logs,
evidence -- into Cloud Trace. Three real leak paths were found and fixed:

1. agent/main.py's "sre.query": query[:250] span attribute -- the raw user query,
   which can embed operational/log detail from the alert that triggered it.
2. agent/otel.py's trace_node() exception handling -- span.record_exception(exc),
   Status(..., str(exc)), and "sre.node.error": str(exc) all sent the exception's
   own message text into the span.
3. agent/main.py's outer "sre_agent.investigation" span used start_as_current_span()'s
   default record_exception=True/set_status_on_exception=True -- OTel's OWN framework
   would auto-capture an unhandled exception's message even with (1) and (2) fixed.

Uses a real OpenTelemetry TracerProvider backed by InMemorySpanExporter (no network,
no GCP credentials needed) so these tests inspect REAL exported span data, not a
simulation of what OTel does.
"""
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent.otel as otel_mod

SECRET = "SECRET-MARKER-9f3a7c2e-db_password=hunter2-k8s_log_excerpt"


@pytest.fixture
def in_memory_tracer(monkeypatch):
    """Real OTel tracer + exporter, entirely in-process. Monkeypatches
    agent.otel.get_tracer() (module-level cache) so trace_node()/main.py's own
    tracer.start_as_current_span() calls use it instead of the real Cloud Trace
    exporter."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    monkeypatch.setattr(otel_mod, "_tracer", tracer)
    monkeypatch.setattr(otel_mod, "_provider", provider)
    monkeypatch.setattr(otel_mod, "get_tracer", lambda: tracer)
    return tracer, exporter


def _all_span_text(span) -> str:
    """Every string that could possibly appear on an exported span: attributes,
    status description, and event (record_exception) attributes."""
    parts = []
    for v in span.attributes.values():
        parts.append(str(v))
    if span.status and span.status.description:
        parts.append(span.status.description)
    for event in span.events:
        parts.append(event.name)
        for v in event.attributes.values():
            parts.append(str(v))
    return " ".join(parts)


def test_trace_node_exception_message_never_reaches_span_attribute_status_or_event(in_memory_tracer):
    tracer, exporter = in_memory_tracer

    @otel_mod.trace_node("test.node")
    def failing_node(state):
        raise ValueError(f"boom: {SECRET}")

    with pytest.raises(ValueError):
        failing_node({"run_id": "run_test", "investigation": {}, "evidence_ids": [],
                       "tool_history": []})

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    full_text = _all_span_text(span)
    assert SECRET not in full_text, f"secret leaked into span data: {full_text}"

    # Safe metadata must still be present.
    assert span.attributes.get("sre.node.success") is False
    assert span.attributes.get("sre.node.error_type") == "ValueError"
    assert span.attributes.get("sre.node") == "test.node"
    assert "sre.node.error" not in span.attributes  # the OLD unsafe attribute is gone


def test_trace_node_success_path_keeps_normal_metadata_attributes(in_memory_tracer):
    tracer, exporter = in_memory_tracer

    @otel_mod.trace_node("test.node")
    def ok_node(state):
        return {"investigation": {"status": "done", "confidence": 0.8},
                "evidence_ids": ["ev_001"], "errors": []}

    result = ok_node({
        "run_id": "run_test", "investigation": {"current_step": 1}, "evidence_ids": [],
        "tool_history": [], "resolved_context": {"cluster_name": "sre-test-cluster"},
    })
    assert result["investigation"]["status"] == "done"

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.attributes.get("sre.node.success") is True
    assert span.attributes.get("sre.run_id") == "run_test"
    assert span.attributes.get("sre.cluster") == "sre-test-cluster"
    assert span.attributes.get("sre.result.status") == "done"


def test_investigate_never_puts_the_raw_query_on_the_outer_span(monkeypatch, in_memory_tracer):
    tracer, exporter = in_memory_tracer
    import agent.main as main_mod

    class _FailingGraph:
        def invoke(self, state, config=None):
            raise RuntimeError(f"graph exploded: {SECRET}")

    monkeypatch.setattr(main_mod, "_get_graph", lambda: _FailingGraph())

    result = main_mod.investigate({
        "query": f"Pod is crashing, logs show {SECRET}",
        "namespace": "test-incidents", "pod": "test-pod", "cluster": "sre-test-cluster",
    })

    assert result["status"] == "failed"

    spans = exporter.get_finished_spans()
    assert len(spans) >= 1
    outer = next(s for s in spans if s.name == "sre_agent.investigation")
    full_text = _all_span_text(outer)
    assert SECRET not in full_text, f"secret leaked into outer span data: {full_text}"
    assert "sre.query" not in outer.attributes  # the old unsafe attribute is gone

    # Safe metadata must still be present.
    assert outer.attributes.get("sre.cluster.requested") == "sre-test-cluster"
    assert outer.attributes.get("sre.namespace.requested") == "test-incidents"
    assert outer.attributes.get("sre.severity") == "unknown"
