"""Unit tests for PRODUCTION-LAUNCH-PLAN.md Priority 10 (logging/metrics/alert validation).

Verifies the new structured-log fields are ACTUALLY emitted by real node execution — not
just present in the code. The only thing mocked is the Cloud Logging network boundary
(google.cloud.logging.Client), following the same pattern the rest of this suite already
uses (see test_rca_builder_integration.py's _mock_llm_json comment) — the business logic
that builds each log entry runs for real and the mock only captures what it produced.

Covers:
- rca_builder._write_observability_log: trace_id, status, mcp_latency_s, model_latency_s,
  total_latency_s, evidence_storage_ok/failed_count/failed_ids, pagerduty_incident_id
  (placeholder), and reuse (not duplication) of Task 1's cluster_routing_method/reason.
- mcp_router's routing-failure structured log (sre-agent-routing-failures).
- gcs_client's evidence-storage-failure structured log (sre-agent-evidence-storage-failures).
"""
from __future__ import annotations

import time

import agent.gcs_client as gcs_client_mod
import agent.nodes.mcp_router as mcp_router_mod
import agent.nodes.rca_builder as rca_builder_mod
from tests.conftest import make_evidence, make_state, make_tool_history_entry


# ── Shared fake Cloud Logging client — captures log_struct calls, makes no network call ──

class _FakeLogger:
    def __init__(self, name: str, sink: list):
        self._name = name
        self._sink = sink

    def log_struct(self, entry: dict, severity: str = "INFO") -> None:
        self._sink.append({"logger": self._name, "entry": entry, "severity": severity})


class _FakeLoggingClient:
    def __init__(self, sink: list):
        self._sink = sink

    def logger(self, name: str) -> _FakeLogger:
        return _FakeLogger(name, self._sink)


def _patch_cloud_logging(monkeypatch) -> list:
    """Patches google.cloud.logging.Client so every `from google.cloud import logging as
    cloud_logging; cloud_logging.Client()` call site in the codebase picks up the fake —
    the import re-resolves the attribute off the already-imported module each call, so
    patching the module attribute here covers gcs_client.py, mcp_router.py, and
    rca_builder.py without needing per-module patches."""
    import google.cloud.logging as cloud_logging_pkg

    sink: list = []
    monkeypatch.setattr(cloud_logging_pkg, "Client", lambda *a, **k: _FakeLoggingClient(sink))
    return sink


# ── rca_builder observability log ──────────────────────────────────────────────────────

def _rca_state(errors: list | None = None) -> dict:
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["exit 137"]),
        "ev_002": make_evidence("ev_002", "list_events", key_facts=["OOMKilled"]),
    }
    # One evidence item that failed to write to GCS — exercises evidence_storage_* fields.
    evidence_store["ev_003"] = {
        **make_evidence("ev_003", "get_previous_logs", key_facts=[]),
        "gcs_write_failed": True,
    }
    tool_history = [
        make_tool_history_entry(0, "describe_pod_detail"),
        make_tool_history_entry(1, "list_events"),
    ]
    tool_history[0]["duration_s"] = 1.5
    tool_history[1]["duration_s"] = 2.5

    state = make_state("OOMKilled", evidence_store, tool_history, started_at=time.time() - 10)
    state["run_id"] = "run_test_obs"
    state["incident_id"] = "inc_test_obs"
    state["working_theory"] = "Container OOMKilled"
    state["incident_envelope"] = {"user_query": "Pod x OOMKilled", "memory_context": ""}
    state["sources_skipped"] = []
    state["evaluation_ids"] = []
    state["errors"] = errors or []
    # Task 1's routing fields — rca_builder must REUSE these, not invent a second field.
    state["resolved_context"]["cluster_routing_method"] = "exact_id"
    state["resolved_context"]["cluster_routing_reason"] = "cluster_hint matched canonical id exactly"
    state["investigation"]["completeness"] = {
        "score": 0.9, "band": "complete", "gaps": [],
        "evidence_domains_present": [], "missing_required_domains": [],
    }
    state["investigation"]["tokens_total"] = 0
    state["investigation"]["estimated_cost_usd"] = 0.0
    state["investigation"]["loop_exit_reason"] = "confidence_sufficient"
    return state


def _mock_llm_json(monkeypatch):
    response = {
        "likely_root_cause": "Container OOMKilled, exit code 137 (ev_001, ev_002)",
        "claims": [{
            "text": "Container OOMKilled, exit code 137",
            "claim_type": "observed_fact",
            "supporting_evidence_ids": ["ev_001", "ev_002"],
        }],
        "alternative_hypotheses_considered": [],
        "evidence_chain": ["ev_001", "ev_002"],
        "evidence_gaps": [],
        "reasoning_trace": ["exit 137 observed"],
        "suggested_remediation": ["Increase memory limit"],
        "sources_skipped": [],
    }
    usage = {
        "input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 50,
        "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 150,
        "billable_output_tokens": 50, "cost_usd": 0.0001,
        "provider": "gemini", "model": "gemini-2.5-pro", "duration_s": 0.1,
    }
    monkeypatch.setattr(rca_builder_mod, "llm_json", lambda *a, **k: (dict(response), dict(usage)))


def test_rca_builder_log_has_all_priority10_fields_and_reuses_task1_routing_fields(monkeypatch):
    sink = _patch_cloud_logging(monkeypatch)
    _mock_llm_json(monkeypatch)

    state = _rca_state(errors=[])
    rca_builder_mod.rca_builder(state)

    assert len(sink) == 1, "expected exactly one structured log entry written"
    call = sink[0]
    assert call["logger"] == "sre-agent-investigations"
    entry = call["entry"]

    # trace_id — present as a key even with no active OTel span (empty string, never absent).
    assert "trace_id" in entry
    assert isinstance(entry["trace_id"], str)

    # top-level status — restores the pre-existing `errors` log-based metric.
    assert entry["status"] == "success"

    # PagerDuty placeholder field — present, None until Priority 2 wires a real value.
    assert "pagerduty_incident_id" in entry
    assert entry["pagerduty_incident_id"] is None

    # Agent Gateway / Connect Gateway visibility placeholders — present, None until
    # Priority 3 / gateway-enforce-mode wires a real value (see rca_builder.py comment).
    assert "connect_gateway_status" in entry
    assert entry["connect_gateway_status"] is None
    assert "agent_gateway_authz_mode" in entry
    assert entry["agent_gateway_authz_mode"] is None

    # Latency — mcp_latency_s is the real sum of tool_history duration_s (1.5 + 2.5).
    assert entry["mcp_latency_s"] == 4.0
    assert isinstance(entry["model_latency_s"], float)
    assert entry["model_latency_s"] >= 0.0
    assert entry["total_latency_s"] is not None
    assert entry["total_latency_s"] >= 10.0  # started_at was set 10s in the past

    # Evidence-storage visibility — ev_003 was marked gcs_write_failed=True above.
    assert entry["evidence_storage_ok"] is False
    assert entry["evidence_storage_failed_count"] == 1
    assert entry["evidence_storage_failed_ids"] == ["ev_003"]

    # Task 1's routing fields — reused verbatim, not duplicated under a new name.
    assert entry["cluster_routing_method"] == "exact_id"
    assert entry["cluster_routing_reason"] == "cluster_hint matched canonical id exactly"


def test_rca_builder_status_is_error_when_run_recorded_errors(monkeypatch):
    """The pre-existing `errors` log-based metric (iac/agent/monitoring.tf) filters on
    jsonPayload.status=="error" — this proves that filter can now actually fire."""
    sink = _patch_cloud_logging(monkeypatch)
    _mock_llm_json(monkeypatch)

    state = _rca_state(errors=["tool=get_current_logs error=HTTP 500"])
    rca_builder_mod.rca_builder(state)

    entry = sink[0]["entry"]
    assert entry["status"] == "error"


def test_rca_builder_clean_run_has_no_evidence_storage_failures(monkeypatch):
    sink = _patch_cloud_logging(monkeypatch)
    _mock_llm_json(monkeypatch)

    state = _rca_state(errors=[])
    # Remove the deliberately-failed evidence item for this positive control.
    del state["evidence_store"]["ev_003"]
    state["evidence_ids"] = ["ev_001", "ev_002"]

    rca_builder_mod.rca_builder(state)

    entry = sink[0]["entry"]
    assert entry["evidence_storage_ok"] is True
    assert entry["evidence_storage_failed_count"] == 0
    assert entry["evidence_storage_failed_ids"] == []


# ── mcp_router routing-failure log ─────────────────────────────────────────────────────

def _router_state(cluster_name: str) -> dict:
    return {
        "run_id": "run_test_routing_log",
        "resolved_context": {
            "cluster_name": cluster_name,
            "incident_type": "OOMKilled",
            "namespace": "test-incidents",
            "pod": "test-pod",
        },
        "investigation": {
            "current_step": 1,
            "task_plan": "check pod status",
            "primary_gap": "pod status unknown",
            "min_steps": 2,
        },
        "sources_skipped": [],
        "evidence_ids": [],
        "tool_history": [],
    }


def test_mcp_router_safe_stop_writes_routing_failure_structured_log(monkeypatch):
    sink = _patch_cloud_logging(monkeypatch)
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: {})

    state = _router_state("prod-cluster-us-east1")
    result = mcp_router_mod.mcp_router(state)

    assert result["current_action"]["tool"] == "done"
    assert len(sink) == 1
    assert sink[0]["logger"] == "sre-agent-routing-failures"
    entry = sink[0]["entry"]
    assert entry["event"] == "mcp_routing_failure"
    assert entry["run_id"] == "run_test_routing_log"
    assert entry["cluster"] == "prod-cluster-us-east1"
    assert "reason" in entry and entry["reason"]


def test_mcp_router_no_log_written_on_normal_routing(monkeypatch):
    """Positive control — a routable cluster must not emit a routing-failure log."""
    sink = _patch_cloud_logging(monkeypatch)
    registry = {"prod-cluster-us-east1": {"cluster_type": "gke", "enabled": True}}
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: registry)
    monkeypatch.setattr(
        mcp_router_mod, "llm_json",
        lambda *a, **k: (
            {"tool": "done", "arguments": {}, "reason": "no gap left"},
            {
                "input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5,
                "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 15,
                "billable_output_tokens": 5, "cost_usd": 0.0,
                "provider": "gemini", "model": "gemini-2.5-pro", "duration_s": 0.1,
            },
        ),
    )

    state = _router_state("prod-cluster-us-east1")
    mcp_router_mod.mcp_router(state)

    assert sink == []


# ── gcs_client evidence-storage-failure log ────────────────────────────────────────────

def test_write_evidence_permanent_failure_writes_structured_log(monkeypatch):
    sink = _patch_cloud_logging(monkeypatch)
    monkeypatch.setattr(gcs_client_mod.time, "sleep", lambda *_: None)  # skip the real 1s retry wait

    class _AlwaysFailsStorageClient:
        def bucket(self, *_a, **_k):
            raise RuntimeError("simulated GCS outage")

    monkeypatch.setattr(gcs_client_mod, "_get_client", lambda: _AlwaysFailsStorageClient())

    raw_ref = gcs_client_mod.write_evidence("run_test_gcs", "ev_001", {"foo": "bar"})

    assert raw_ref.startswith("gcs_write_failed:")
    assert len(sink) == 1
    assert sink[0]["logger"] == "sre-agent-evidence-storage-failures"
    entry = sink[0]["entry"]
    assert entry["event"] == "evidence_storage_failure"
    assert entry["run_id"] == "run_test_gcs"
    assert entry["evidence_id"] == "ev_001"
    assert "simulated GCS outage" in entry["error"]


def test_write_evidence_success_writes_no_failure_log(monkeypatch):
    sink = _patch_cloud_logging(monkeypatch)

    class _FakeBlob:
        def upload_from_string(self, *_a, **_k):
            return None

    class _FakeBucket:
        def blob(self, _path):
            return _FakeBlob()

    class _FakeStorageClient:
        def bucket(self, _name):
            return _FakeBucket()

    monkeypatch.setattr(gcs_client_mod, "_get_client", lambda: _FakeStorageClient())

    raw_ref = gcs_client_mod.write_evidence("run_test_gcs_ok", "ev_001", {"foo": "bar"})

    assert raw_ref == f"gs://{gcs_client_mod.BUCKET}/run_test_gcs_ok/ev_001.json"
    assert sink == []
