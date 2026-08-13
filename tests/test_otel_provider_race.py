"""Regression tests for issue #130's second follow-up: agent.otel.get_tracer() used to
call trace.set_tracer_provider(provider) and then blindly trust its OWN locally-built
`provider` object as the one backing real spans, without checking whether that call
actually won. OpenTelemetry's set_tracer_provider() can only succeed ONCE per process --
every later call silently no-ops (its own internal warning, no exception). Live logs
confirmed Agent Engine's own managed runtime also calls it, moments after ours, during
its own startup bootstrap.

If our call ever lost that race, get_tracer() would still store our orphaned local
provider in _provider (what flush_traces() flushes) while real spans went through
whichever provider actually won -- silently. No exception, no error log, just spans
that vanish. These tests simulate that race directly and prove get_tracer() now
detects it and attaches its exporter to the ACTUAL active provider (or disables
cleanly if that provider can't accept a processor), instead of trusting its own
local object blindly.
"""
import opentelemetry.exporter.cloud_trace as cloud_trace_mod
import opentelemetry.trace as real_trace
from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent.otel as otel_mod


class _FakeCloudTraceSpanExporter:
    """get_tracer() constructs a real CloudTraceSpanExporter(project_id=...) as part
    of the exact provider-detection logic under test here -- that's intentional, not
    something to bypass. But the real exporter makes actual gRPC calls to Cloud Trace
    on export/flush, which these tests must never do. Standing in only for the
    exporter (not the provider/processor wiring around it) keeps the test exercising
    real get_tracer() logic without touching the network."""

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


def test_get_tracer_attaches_to_actual_active_provider_not_an_orphan(monkeypatch):
    _reset_otel_module_state(monkeypatch)

    # Simulate: something else (Agent Engine's own bootstrap) already won the
    # set_tracer_provider() race. A canary exporter proves which provider real spans
    # actually reach.
    pre_installed = SDKTracerProvider()
    canary_exporter = InMemorySpanExporter()
    pre_installed.add_span_processor(SimpleSpanProcessor(canary_exporter))

    monkeypatch.setattr(real_trace, "set_tracer_provider", lambda p: None)
    monkeypatch.setattr(real_trace, "get_tracer_provider", lambda: pre_installed)

    tracer = otel_mod.get_tracer()
    assert tracer is not None

    # flush_traces() calls force_flush() on _provider -- it must be the provider
    # actually in effect, never an orphan no one reads spans from.
    assert otel_mod._provider is pre_installed

    with tracer.start_as_current_span("canary"):
        pass

    spans = canary_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "canary"


def test_get_tracer_disables_cleanly_when_active_provider_is_a_stub(monkeypatch):
    _reset_otel_module_state(monkeypatch)

    class _StubProvider:
        """Mirrors OTel's default no-op ProxyTracerProvider: no add_span_processor."""

    stub = _StubProvider()
    monkeypatch.setattr(real_trace, "set_tracer_provider", lambda p: None)
    monkeypatch.setattr(real_trace, "get_tracer_provider", lambda: stub)

    tracer = otel_mod.get_tracer()

    assert tracer is None
    assert otel_mod._provider is None


def test_get_tracer_normal_path_when_our_provider_wins(monkeypatch):
    """Sanity check: when our own set_tracer_provider() call DOES win (the common
    case, confirmed live for 11/11 workers in one deploy), get_tracer() still returns
    a working tracer backed by our own provider, unchanged from before this fix."""
    _reset_otel_module_state(monkeypatch)

    installed = {}

    def _fake_set(p):
        installed["provider"] = p

    monkeypatch.setattr(real_trace, "set_tracer_provider", _fake_set)
    monkeypatch.setattr(real_trace, "get_tracer_provider", lambda: installed["provider"])

    tracer = otel_mod.get_tracer()

    assert tracer is not None
    assert otel_mod._provider is installed["provider"]
