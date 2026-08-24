"""Regression test for the new GCS evidence-write trace span added in
gcs_client.py (observability review,
docs/management/observability-architecture-review-2026-08-23.md section 7):
before this, write_evidence() had zero tracing -- only plain log lines --
leaving evidence storage as a blind spot in the
PagerDuty -> ... -> evidence storage -> RCA correlation chain.

Uses a real OpenTelemetry TracerProvider backed by InMemorySpanExporter, same
pattern as tests/test_otel_span_content_safety.py.
"""
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent.otel as otel_mod
import agent.gcs_client as gcs_client_mod


@pytest.fixture
def in_memory_tracer(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    # write_evidence() does `from agent.otel import get_tracer` as a LOCAL
    # import -- patch the module attribute it re-reads on every call.
    monkeypatch.setattr(otel_mod, "get_tracer", lambda: tracer)
    return tracer, exporter


class _FakeBlob:
    def __init__(self, sink):
        self._sink = sink

    def upload_from_string(self, data, content_type=None):
        self._sink["data"] = data


class _FakeBucket:
    def __init__(self, sink):
        self._sink = sink

    def blob(self, path):
        return _FakeBlob(self._sink)


class _FakeClient:
    def __init__(self, sink):
        self._sink = sink

    def bucket(self, name):
        return _FakeBucket(self._sink)


def test_gcs_span_created_with_correct_attributes_on_success(monkeypatch, in_memory_tracer):
    tracer, exporter = in_memory_tracer
    sink = {}
    monkeypatch.setattr(gcs_client_mod, "_get_client", lambda: _FakeClient(sink))

    raw_ref = gcs_client_mod.write_evidence("run1", "ev1", {"k": "v"})

    assert raw_ref.startswith("gs://")
    spans = exporter.get_finished_spans()
    write_spans = [s for s in spans if s.name == "gcs.write_evidence"]
    assert len(write_spans) == 1
    attrs = write_spans[0].attributes
    assert attrs["run_id"] == "run1"
    assert attrs["evidence_id"] == "ev1"
    assert attrs["bucket"] == gcs_client_mod.BUCKET
    assert attrs["ok"] is True
    assert attrs["duration_ms"] >= 0


def test_gcs_span_records_failure_after_retries_exhausted(monkeypatch, in_memory_tracer):
    tracer, exporter = in_memory_tracer

    class _AlwaysFailsClient:
        def bucket(self, name):
            raise RuntimeError("permission denied")

    monkeypatch.setattr(gcs_client_mod, "_get_client", lambda: _AlwaysFailsClient())
    monkeypatch.setattr(gcs_client_mod.time, "sleep", lambda s: None)  # skip the real retry delay
    monkeypatch.setattr(gcs_client_mod, "_log_evidence_storage_failure", lambda *a, **k: None)

    result = gcs_client_mod.write_evidence("run1", "ev1", {"k": "v"})

    assert result.startswith("gcs_write_failed:")
    spans = exporter.get_finished_spans()
    write_spans = [s for s in spans if s.name == "gcs.write_evidence"]
    assert len(write_spans) == 1
    assert write_spans[0].attributes["ok"] is False


def test_write_evidence_still_works_when_tracer_is_none(monkeypatch):
    monkeypatch.setattr(otel_mod, "get_tracer", lambda: None)
    sink = {}
    monkeypatch.setattr(gcs_client_mod, "_get_client", lambda: _FakeClient(sink))

    raw_ref = gcs_client_mod.write_evidence("run1", "ev1", {"k": "v"})

    assert raw_ref.startswith("gs://")
