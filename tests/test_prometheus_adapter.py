"""Section 6 worked example: Prometheus adapter, tested against fixture responses
shaped like Prometheus's own documented /api/v1/query_range payload -- see
agent/sources/prometheus_adapter.py's module docstring for why this is a narrow
custom adapter rather than a wrapped third-party MCP server, and for the explicit
NOT VERIFIED status against a real live instance."""
from unittest.mock import MagicMock, patch

import pytest

import agent.source_catalog as catalog
from agent.sources.prometheus_adapter import PrometheusQueryError, query_range


def _enable_prometheus(monkeypatch, **overrides):
    patched = dict(catalog.SOURCE_CATALOG)
    patched["prometheus"] = {**patched["prometheus"], "enabled": True, **overrides}
    monkeypatch.setattr(catalog, "SOURCE_CATALOG", patched)
    # prometheus_adapter imported SOURCE_CATALOG by reference at module load time --
    # patch its own binding too so the adapter sees the same enabled state.
    import agent.sources.prometheus_adapter as adapter_mod
    monkeypatch.setattr(adapter_mod, "SOURCE_CATALOG", patched)


def test_disabled_source_is_never_queryable_directly(monkeypatch):
    """Even calling the adapter function directly (bypassing the router entirely)
    must refuse to run while the catalog entry is disabled -- the enabled flag is
    the actual gate, not just something the router happens to check first."""
    with pytest.raises(PrometheusQueryError, match="not enabled"):
        query_range("sre-lab", "up", 0, 100)


def test_window_exceeding_limit_is_rejected(monkeypatch):
    _enable_prometheus(monkeypatch)
    with pytest.raises(PrometheusQueryError, match="exceeds"):
        query_range("sre-lab", "up", 0, 7200)  # 2 hours > 3600s max


def test_invalid_window_rejected(monkeypatch):
    _enable_prometheus(monkeypatch)
    with pytest.raises(PrometheusQueryError, match="invalid time window"):
        query_range("sre-lab", "up", 100, 50)  # end before start


def test_missing_endpoint_rejected(monkeypatch):
    _enable_prometheus(monkeypatch)
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)
    with pytest.raises(PrometheusQueryError, match="no Prometheus endpoint"):
        query_range("sre-lab", "up", 0, 100)


def test_successful_query_returns_normalized_evidence(monkeypatch):
    _enable_prometheus(monkeypatch)
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.internal:9090")

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "status": "success",
        "data": {"result": [
            {"metric": {"pod": "crashloop-pod"}, "values": [[1000, "0.5"], [1060, "0.9"]]},
        ]},
    }
    with patch("httpx.get", return_value=fake_response) as mock_get:
        result = query_range("sre-lab", 'rate(container_cpu_usage_seconds_total{pod="crashloop-pod"}[5m])', 0, 100)

    assert result["source"] == "prometheus"
    assert result["cluster_id"] == "sre-lab"
    assert result["retrieval_status"] == "ok"
    assert result["truncated"] is False
    assert len(result["series"]) == 1
    assert result["query_reference"]["promql"].startswith("rate(")
    # The exact request actually sent -- proves the endpoint/params are constructed
    # correctly, not just that SOME call happened.
    call_kwargs = mock_get.call_args.kwargs
    assert call_kwargs["params"]["start"] == 0
    assert call_kwargs["params"]["end"] == 100


def test_oversized_result_is_truncated_not_silently_dropped(monkeypatch):
    _enable_prometheus(monkeypatch, query_limits={
        "max_window_seconds": 3600, "max_result_series": 2, "timeout_s": 10,
    })
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.internal:9090")

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "status": "success",
        "data": {"result": [{"metric": {"i": i}, "values": []} for i in range(5)]},
    }
    with patch("httpx.get", return_value=fake_response):
        result = query_range("sre-lab", "up", 0, 100)

    assert result["truncated"] is True
    assert len(result["series"]) == 2


def test_prometheus_error_status_raises_not_fabricates(monkeypatch):
    """A malformed/rejected query must surface as an explicit failure -- never a
    fabricated empty-but-'ok' result."""
    _enable_prometheus(monkeypatch)
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.internal:9090")

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"status": "error", "error": "bad_data: invalid query"}
    with patch("httpx.get", return_value=fake_response):
        with pytest.raises(PrometheusQueryError, match="non-success status"):
            query_range("sre-lab", "not a valid promql (((", 0, 100)


def test_network_failure_raises_clean_error_not_unhandled_exception(monkeypatch):
    _enable_prometheus(monkeypatch)
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.internal:9090")

    with patch("httpx.get", side_effect=ConnectionError("connection refused")):
        with pytest.raises(PrometheusQueryError, match="Prometheus query failed"):
            query_range("sre-lab", "up", 0, 100)
