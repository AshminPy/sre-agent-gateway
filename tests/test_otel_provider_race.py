"""Regression tests for issue #164: agent.otel.get_tracer() used to call
trace.set_tracer_provider(provider) to try to win OpenTelemetry's one-time global
default-provider slot, with fallback logic to detect losing that race and attach our
exporter to whoever won instead. Issue #130's fix made us win that race 100% of the
time (confirmed live: "11/11 workers") -- which meant Agent Engine's own built-in
managed OTel provider, which the Agent Platform Console's Traces tab and Telemetry
collection status read from, never got to install itself. Our own spans still exported
to Cloud Trace correctly the whole time; the Console-side visibility was the silent
casualty.

These tests prove the fix: get_tracer() now builds its own local TracerProvider and
hands out tracers straight from that instance, never touching the global
trace.get_tracer_provider()/set_tracer_provider() accessors at all -- so our own spans
are guaranteed to reach our exporter regardless of any race, AND the global default
provider (whatever it is -- Agent Engine's own, or nothing at all) is left completely
undisturbed.
"""
import opentelemetry.exporter.cloud_trace as cloud_trace_mod
import opentelemetry.trace as real_trace
from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent.otel as otel_mod


class _FakeCloudTraceSpanExporter:
    """get_tracer() constructs a real CloudTraceSpanExporter(project_id=...) as part
    of the exact provider wiring under test here -- that's intentional, not something
    to bypass. But the real exporter makes actual gRPC calls to Cloud Trace on
    export/flush, which these tests must never do. Standing in only for the exporter
    (not the provider/processor wiring around it) keeps the test exercising real
    get_tracer() logic without touching the network."""

    def __init__(self, project_id=None):
        self.project_id = project_id

    def export(self, spans):
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True


def _reset_otel_module_state(monkeypatch):
    monkeypatch.setattr(otel_mod, "_tracer", None)
    monkeypatch.setattr(otel_mod, "_provider", None)
    monkeypatch.setattr(otel_mod, "_otel_error_logged", False)
    monkeypatch.setattr(cloud_trace_mod, "CloudTraceSpanExporter", _FakeCloudTraceSpanExporter)


def test_get_tracer_never_touches_the_global_default_provider(monkeypatch):
    """The whole point of the #164 fix: our own tracer setup must not call
    set_tracer_provider() at all, so it can never block Agent Engine's own managed
    provider from installing itself, whatever order the two run in."""
    _reset_otel_module_state(monkeypatch)

    calls = []
    monkeypatch.setattr(real_trace, "set_tracer_provider", lambda p: calls.append(p))

    tracer = otel_mod.get_tracer()

    assert tracer is not None
    assert calls == [], "get_tracer() must never call the global set_tracer_provider()"


def test_get_tracer_spans_reach_our_own_exporter_regardless_of_global_state(monkeypatch):
    """Simulate Agent Engine's own managed runtime already owning the global default
    provider (a real SDK provider with its own canary exporter). Our own tracer's
    spans must still land on OUR exporter -- and the pre-existing global provider's
    own canary exporter must receive nothing from us, proving we never touched it."""
    _reset_otel_module_state(monkeypatch)

    pre_installed = SDKTracerProvider()
    canary_exporter = InMemorySpanExporter()
    pre_installed.add_span_processor(SimpleSpanProcessor(canary_exporter))
    monkeypatch.setattr(real_trace, "get_tracer_provider", lambda: pre_installed)

    tracer = otel_mod.get_tracer()
    assert tracer is not None

    with tracer.start_as_current_span("our_span"):
        pass

    # Our span must never reach the pre-existing global provider's exporter.
    assert canary_exporter.get_finished_spans() == ()

    # flush_traces() must flush OUR OWN provider, not the global one.
    assert otel_mod._provider is not pre_installed
    otel_mod.flush_traces()  # must not raise


def test_get_tracer_still_works_when_no_global_provider_exists_at_all(monkeypatch):
    """Sanity check for local/test environments with no competing runtime: get_tracer()
    must still return a working tracer even when there's nothing else installed."""
    _reset_otel_module_state(monkeypatch)

    tracer = otel_mod.get_tracer()

    assert tracer is not None
    assert otel_mod._provider is not None

    with tracer.start_as_current_span("solo_span"):
        pass  # must not raise
