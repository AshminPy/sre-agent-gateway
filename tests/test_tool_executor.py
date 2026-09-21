"""Regression test for issue #71: tool_history/latest_tool_result must record the tool
and mcp_source that ACTUALLY executed (call_tool()'s own return value), not the
pre-call request -- call_tool() can auto-fall back to a different tool/source
(GKE Remote MCP failure -> custom MCP, or issue #70's broadened retry).
"""
import agent.nodes.tool_executor as tool_executor_mod


def _state_with_action(tool: str, mcp_source: str) -> dict:
    return {
        "run_id": "run_test",
        "current_action": {"tool": tool, "mcp_source": mcp_source, "arguments": {}},
        "resolved_context": {"cluster_name": "sre-test-cluster"},
        "investigation": {"current_step": 1},
    }


def test_fallback_to_different_source_is_recorded_not_the_original_request(monkeypatch):
    # Requested GKE Remote MCP, but call_tool() actually fell back to custom k8s_mcp.
    monkeypatch.setattr(
        tool_executor_mod, "call_tool",
        lambda **kwargs: {
            "ok": True, "result": {"data": "x"}, "duration_s": 0.5,
            "tool": "describe_pod_detail",  # the custom-MCP equivalent tool name
            "mcp_source": "k8s_mcp",         # what actually ran
        },
    )
    state = _state_with_action(tool="describe_k8s_resource", mcp_source="gke_remote_mcp")
    update = tool_executor_mod.tool_executor(state)

    record = update["tool_history"][0]
    assert record["tool"] == "describe_pod_detail"
    assert record["mcp_source"] == "k8s_mcp"
    assert update["latest_tool_result"]["tool"] == "describe_pod_detail"
    assert update["latest_tool_result"]["mcp_source"] == "k8s_mcp"


def test_fallback_recorded_correctly_on_the_failure_path_too(monkeypatch):
    monkeypatch.setattr(
        tool_executor_mod, "call_tool",
        lambda **kwargs: {
            "ok": False, "error": "boom", "duration_s": 0.1,
            "tool": "describe_pod_detail",
            "mcp_source": "k8s_mcp",
        },
    )
    state = _state_with_action(tool="describe_k8s_resource", mcp_source="gke_remote_mcp")
    update = tool_executor_mod.tool_executor(state)

    record = update["tool_history"][0]
    assert record["tool"] == "describe_pod_detail"
    assert record["mcp_source"] == "k8s_mcp"
    assert "tool=describe_pod_detail" in update["errors"][0]
    assert update["latest_tool_result"]["tool"] == "describe_pod_detail"
    assert update["latest_tool_result"]["mcp_source"] == "k8s_mcp"


def test_no_fallback_still_records_the_requested_tool_correctly(monkeypatch):
    monkeypatch.setattr(
        tool_executor_mod, "call_tool",
        lambda **kwargs: {
            "ok": True, "result": {"data": "x"}, "duration_s": 0.2,
            "tool": "describe_k8s_resource", "mcp_source": "gke_remote_mcp",
        },
    )
    state = _state_with_action(tool="describe_k8s_resource", mcp_source="gke_remote_mcp")
    update = tool_executor_mod.tool_executor(state)

    record = update["tool_history"][0]
    assert record["tool"] == "describe_k8s_resource"
    assert record["mcp_source"] == "gke_remote_mcp"


def _state_with_args(tool: str, mcp_source: str, arguments: dict) -> dict:
    return {
        "run_id": "run_test",
        "current_action": {"tool": tool, "mcp_source": mcp_source, "arguments": arguments},
        "resolved_context": {"cluster_name": "sre-test-cluster"},
        "investigation": {"current_step": 1},
    }


def test_broadened_retry_records_the_actually_used_args_not_the_scoped_request(monkeypatch):
    # issue #70's broadened retry re-dispatches unscoped when a scoped call 404s --
    # the recorded args must reflect the unscoped call that actually ran, or
    # evidence_extractor/scorer downstream classify evidence against args that
    # were never sent.
    monkeypatch.setattr(
        tool_executor_mod, "call_tool",
        lambda **kwargs: {
            "ok": True, "result": {"data": "x"}, "duration_s": 0.3,
            "tool": "describe_pod_detail", "mcp_source": "k8s_mcp",
            "broadened_after_not_found": "notification-relay",
        },
    )
    state = _state_with_args(
        tool="describe_pod_detail", mcp_source="k8s_mcp",
        arguments={"namespace": "prod", "name": "notification-relay-abc123", "pod_name": "x", "pod": "y"},
    )
    update = tool_executor_mod.tool_executor(state)

    record = update["tool_history"][0]
    assert record["args"] == {"namespace": "prod"}


def test_no_broaden_signal_records_the_original_args_unchanged(monkeypatch):
    monkeypatch.setattr(
        tool_executor_mod, "call_tool",
        lambda **kwargs: {
            "ok": True, "result": {"data": "x"}, "duration_s": 0.3,
            "tool": "describe_pod_detail", "mcp_source": "k8s_mcp",
        },
    )
    state = _state_with_args(
        tool="describe_pod_detail", mcp_source="k8s_mcp",
        arguments={"namespace": "prod", "name": "notification-relay-abc123"},
    )
    update = tool_executor_mod.tool_executor(state)

    record = update["tool_history"][0]
    assert record["args"] == {"namespace": "prod", "name": "notification-relay-abc123"}
