"""Regression test for issue #161: Cloud Trace span fragmentation during native
stream_query() investigations.

Confirmed live (real deployed run, diagnostic logging + direct Cloud Trace
correlation, documented on issue #161): the ambient OTel context that
investigate_stream()'s outer `sre_agent.investigation` span relies on does
NOT survive this generator's own yield/resume boundary -- valid right up to
the edge of the stream loop, gone immediately after the first yield, even
though the OS thread ID is identical throughout. Without a fix, every
LangGraph node span (trace_node) and every gen_ai span starts as its own
disconnected root trace instead of nesting under the outer investigation
span.

This test reproduces that exact failure mode directly (explicitly clearing
the OTel context between generator resumes, matching what was observed live)
and proves the fix (explicit context capture + re-attach in
investigate_stream(), agent/main.py) makes every child span nest under the
outer span regardless.
"""
from opentelemetry import context as otel_context
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent.main as main_mod
from agent.main import investigate_stream


def _fresh_tracer():
    """A real, isolated TracerProvider + in-memory exporter per test -- not the
    module-global one, so tests don't interfere with each other's spans."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test_161"), exporter


class _FakeGraphContextLossBetweenYields:
    """Fake compiled graph whose .stream() creates one real child span per
    snapshot (simulating trace_node's per-node span). It does NOT itself
    tamper with the OTel context -- LangGraph's real node execution doesn't
    either. The context loss being reproduced happens one level up, at
    investigate_stream()'s OWN yield/resume boundary (simulated by the test
    driver below, between calls to next() on investigate_stream()'s
    generator) -- exactly where the live evidence on issue #161 pinned it
    (context valid through B, gone by the first C). This span-creation code
    only sees whatever context is ambient when graph.stream() is advanced,
    which is exactly what agent/main.py's investigate_stream() fix controls.
    """

    def __init__(self, tracer, num_steps=3):
        self._tracer = tracer
        self._num_steps = num_steps

    def stream(self, state, config=None, stream_mode=None):
        for i in range(self._num_steps):
            with self._tracer.start_as_current_span(f"langgraph.fake_node_{i}"):
                pass
            yield {
                "run_id": "run_161_test", "step": i,
                "investigation": {"status": "done" if i == self._num_steps - 1 else "running",
                                   "confidence": 0.5, "confidence_band": "escalate"},
                "resolved_context": {}, "errors": [], "evidence_ids": [],
                "tool_history": [], "evidence_store": {}, "final_summary": {},
            }


def _spans_by_name(exporter):
    return {s.name: s for s in exporter.get_finished_spans()}


def test_child_spans_nest_under_outer_span_despite_context_loss_between_yields(monkeypatch):
    tracer, exporter = _fresh_tracer()
    monkeypatch.setattr(main_mod, "get_tracer", lambda: tracer, raising=False)

    import agent.otel as otel_mod
    monkeypatch.setattr(otel_mod, "get_tracer", lambda: tracer)

    fake_graph = _FakeGraphContextLossBetweenYields(tracer, num_steps=3)
    monkeypatch.setattr(main_mod, "_get_graph", lambda: fake_graph)

    gen = investigate_stream({"query": "test", "cluster": "c", "namespace": "n"})
    try:
        while True:
            next(gen)
            # Simulate the real platform behavior between chunks: clear
            # whatever context is current, exactly as observed live.
            otel_context.attach(otel_context.Context())
    except StopIteration:
        pass

    spans = _spans_by_name(exporter)
    assert "sre_agent.investigation" in spans
    outer = spans["sre_agent.investigation"]
    outer_trace_id = outer.context.trace_id

    node_spans = [s for name, s in spans.items() if name.startswith("langgraph.fake_node_")]
    assert len(node_spans) == 3, "all 3 fake node spans must have been created"

    for s in node_spans:
        assert s.context.trace_id == outer_trace_id, (
            f"{s.name} landed in a different trace ({s.context.trace_id:032x}) "
            f"than the outer investigation span ({outer_trace_id:032x}) -- "
            "this is exactly the #161 fragmentation bug"
        )
        assert s.parent is not None, f"{s.name} has no parent span at all"
        assert s.parent.span_id == outer.context.span_id, (
            f"{s.name}'s parent_span_id does not point back to the outer span"
        )


def test_investigation_still_completes_normally_with_the_fix(monkeypatch):
    """The context-propagation fix must not change investigation behavior --
    the final result is still produced correctly."""
    tracer, exporter = _fresh_tracer()
    import agent.otel as otel_mod
    monkeypatch.setattr(otel_mod, "get_tracer", lambda: tracer)
    monkeypatch.setattr(main_mod, "get_tracer", lambda: tracer, raising=False)

    fake_graph = _FakeGraphContextLossBetweenYields(tracer, num_steps=2)
    monkeypatch.setattr(main_mod, "_get_graph", lambda: fake_graph)

    gen = investigate_stream({"query": "test", "cluster": "c", "namespace": "n"})
    result = None
    try:
        while True:
            next(gen)
            otel_context.attach(otel_context.Context())
    except StopIteration as stop:
        result = stop.value

    assert result is not None
    assert result["run_id"] == "run_161_test"
    assert result["status"] == "done"
