"""agent/sources/prometheus_adapter.py — narrow, read-only Prometheus query adapter.

STATUS: NOT VERIFIED against a live Prometheus instance. No Prometheus deployment
exists in this environment (personal test GCP project). Built and unit-tested
against Prometheus's own documented HTTP API shape
(https://prometheus.io/docs/prometheus/latest/querying/api/#range-queries) using
fixture responses, not a real server. Do not enable agent/source_catalog.py's
"prometheus" entry in any real deployment without first running this adapter
against a real instance and completing a live-validation pass.

Research basis (2026-09-07, WebSearch): no single official/canonical Prometheus
MCP server exists — several community implementations were found
(pab1it0/prometheus-mcp-server, giantswarm/mcp-prometheus, others), none clearly
dominant or maintained by the Prometheus project itself. Grafana ships an
official MCP server (grafana/mcp-grafana) that CAN proxy to a Prometheus
datasource, but per this section's own explicit caution, Grafana reachability
does not prove the underlying datasource is reachable — that would be a
separate, separately-authorized catalog entry, never assumed equivalent to this
one. Given no single trustworthy official Prometheus MCP exists, this is a
narrow custom adapter against Prometheus's own REST API, not a wrapped
third-party MCP server, per the parent runbook's "build a narrow custom adapter
only where needed" guidance (docs/runbooks/add-mcp-server.md).

Read-only: only ever calls Prometheus's /api/v1/query_range (GET). No write/admin
endpoint is called from anywhere in this module.
"""
import time
from typing import Any, Dict

import httpx

from agent.source_catalog import SOURCE_CATALOG

import os


class PrometheusQueryError(RuntimeError):
    """Raised for anything that must surface as an explicit evidence gap, never
    fabricated data — an unreachable/erroring Prometheus produces a clear,
    honest failure, exactly like a failed Kubernetes tool call does."""


def query_range(cluster_id: str, promql: str, start_ts: float, end_ts: float, step_s: int = 60) -> Dict[str, Any]:
    """Bounded PromQL range query.

    Enforces this catalog entry's query_limits: the requested window cannot
    exceed max_window_seconds, and a response exceeding max_result_series is
    truncated with truncated=True (never silently dropped without saying so).

    Returns evidence normalized per this section's required field list:
    source, cluster binding, resource identity (the PromQL query string itself
    — there is no single K8s-object identity for a metric query), event time
    (the query's own start/end window), collection time (wall-clock now),
    query reference (the exact PromQL + window + step actually sent), raw
    evidence reference (the returned series ARE the raw+normalized evidence in
    one — this adapter's scope is narrow enough that a separate raw-storage
    reference would just point back at the same dict), truncation, redaction
    (Prometheus label/metric names are not expected to carry secrets, but the
    field is always present so every source's evidence shape is uniform), and
    retrieval status.
    """
    entry = SOURCE_CATALOG.get("prometheus", {})
    if not entry.get("enabled"):
        raise PrometheusQueryError(
            "prometheus source is not enabled in the catalog — this adapter must "
            "never be called directly, bypassing that check"
        )

    limits = entry.get("query_limits", {})
    max_window = limits.get("max_window_seconds", 3600)
    max_series = limits.get("max_result_series", 50)
    timeout_s  = limits.get("timeout_s", 10)

    window = end_ts - start_ts
    if window <= 0:
        raise PrometheusQueryError(f"invalid time window: start={start_ts} end={end_ts}")
    if window > max_window:
        raise PrometheusQueryError(
            f"requested window {window}s exceeds this catalog entry's "
            f"max_window_seconds={max_window}s"
        )

    endpoint = os.environ.get(entry.get("endpoint_env", "PROMETHEUS_URL"), "").strip()
    if not endpoint:
        raise PrometheusQueryError(
            f"no Prometheus endpoint configured ({entry.get('endpoint_env')} is unset)"
        )

    collection_time = time.time()
    try:
        resp = httpx.get(
            f"{endpoint.rstrip('/')}/api/v1/query_range",
            params={"query": promql, "start": start_ts, "end": end_ts, "step": step_s},
            timeout=timeout_s,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        raise PrometheusQueryError(f"Prometheus query failed: {exc}") from exc

    if payload.get("status") != "success":
        raise PrometheusQueryError(
            f"Prometheus returned non-success status: {payload.get('status')!r} "
            f"error={payload.get('error')!r}"
        )

    result = payload.get("data", {}).get("result", [])
    truncated = len(result) > max_series
    if truncated:
        result = result[:max_series]

    return {
        "source":            "prometheus",
        "cluster_id":        cluster_id,
        "resource_identity": promql,
        "event_time_start":  start_ts,
        "event_time_end":    end_ts,
        "collection_time":   collection_time,
        "query_reference":   {"promql": promql, "start": start_ts, "end": end_ts, "step": step_s},
        "series":            result,
        "truncated":         truncated,
        "redacted":          False,
        "retrieval_status":  "ok",
    }
