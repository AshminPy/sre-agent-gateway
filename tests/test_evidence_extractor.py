"""Regression test for the evidence_extractor -> scorer field-name contract (issue #60).

Unlike tests/test_scorer.py, this does NOT use tests/conftest.py's make_evidence() fixture —
that fixture fabricates evidence with a "tool" key directly, which is exactly why the real bug
(evidence_extractor writing "source" instead of "tool") was invisible to every existing test.
This test runs the REAL evidence_extractor() node and feeds its REAL output into the REAL
scorer, so a future field-name drift between the two would fail here even if every other test
still uses fixtures that happen to match the scorer's expectations.
"""
import agent.nodes.evidence_extractor as evidence_extractor_mod
from agent.confidence.evidence_domains import EvidenceDomain, classify_tool
from agent.confidence.scorer import _evidence_domains_present

from tests.conftest import make_state


def _mock_io(monkeypatch, extracted: dict):
    monkeypatch.setattr(evidence_extractor_mod, "write_evidence", lambda *a, **k: "gs://bucket/ev_001.json")
    monkeypatch.setattr(evidence_extractor_mod, "redact", lambda raw: raw)
    monkeypatch.setattr(
        evidence_extractor_mod, "llm_json",
        lambda *a, **k: (dict(extracted), {
            "input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5,
            "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 15,
            "billable_output_tokens": 5, "cost_usd": 0.0,
            "provider": "gemini", "model": "gemini-2.5-pro", "duration_s": 0.1,
        }),
    )


def _state_with_tool_result(tool: str, ok: bool = True, error: str | None = None) -> dict:
    state = make_state("OOMKilled", {}, [])
    state["latest_tool_result"] = {
        "ok": ok,
        "tool": tool,
        "mcp_source": "gke_remote_mcp",
        "result": {"status": "Running"} if ok else None,
        "error": error,
    }
    return state


def test_successful_evidence_is_classified_by_the_real_scorer_not_unknown(monkeypatch):
    """The core regression: evidence_extractor's real output, fed into the real scorer's domain
    classifier, must resolve to the tool's actual domain -- not UNKNOWN."""
    _mock_io(monkeypatch, {
        "resource_type": "pod", "resource_id": "test-incidents/test-pod",
        "summary": "Pod is running", "key_facts": ["status: Running"],
    })
    state = _state_with_tool_result("describe_pod_detail")

    result = evidence_extractor_mod.evidence_extractor(state)
    evidence_store = result["evidence_store"]
    ev_entry = evidence_store["ev_001"]

    # The actual field-name contract the scorer depends on.
    assert "tool" in ev_entry, "evidence_extractor must write the tool name under key 'tool'"
    assert ev_entry["tool"] == "describe_pod_detail"
    assert "source" not in ev_entry, "no code reads 'source' from evidence entries -- must not resurface"

    # issue #68: a real per-evidence collection timestamp must be captured.
    assert isinstance(ev_entry.get("collected_at"), float)

    domains = _evidence_domains_present(evidence_store, tool_history=[])
    assert domains["ev_001"] == EvidenceDomain.KUBERNETES_STATUS
    assert classify_tool(ev_entry["tool"]) != EvidenceDomain.UNKNOWN


def test_failed_tool_call_evidence_is_also_classified_correctly(monkeypatch):
    """The error path (latest.get('ok') is False) has its own separate ev_entry construction --
    must carry the same fix."""
    _mock_io(monkeypatch, {})  # unused on the error path, evidence_extractor returns before llm_json
    state = _state_with_tool_result("list_events", ok=False, error="Forbidden")

    result = evidence_extractor_mod.evidence_extractor(state)
    ev_entry = result["evidence_store"]["ev_001"]

    assert ev_entry["tool"] == "list_events"
    assert "source" not in ev_entry
    assert classify_tool(ev_entry["tool"]) == EvidenceDomain.KUBERNETES_EVENTS
    assert isinstance(ev_entry.get("collected_at"), float)  # issue #68: error path too


def test_full_investigation_completeness_score_reflects_real_evidence_domains(monkeypatch):
    """End-to-end: three real evidence_extractor() calls covering OOMKilled's three required
    domains must score as complete via the real scorer -- proving the whole chain, not just the
    field name in isolation."""
    from agent.confidence.policy import POLICY
    from agent.confidence.scorer import score_investigation_completeness

    state = make_state("OOMKilled", {}, [])
    for i, tool in enumerate(["describe_pod_detail", "list_events", "get_previous_logs"], start=1):
        _mock_io(monkeypatch, {
            "resource_type": "pod", "resource_id": "test-incidents/test-pod",
            "summary": f"evidence from {tool}", "key_facts": ["fact"],
        })
        state["latest_tool_result"] = {
            "ok": True, "tool": tool, "mcp_source": "gke_remote_mcp",
            "result": {"data": "x"}, "error": None,
        }
        update = evidence_extractor_mod.evidence_extractor(state)
        state["evidence_store"] = {**state["evidence_store"], **update["evidence_store"]}
        state["evidence_ids"] = state["evidence_ids"] + update["evidence_ids"]
        state["tool_history"] = state["tool_history"] + [
            {"step": i - 1, "tool": tool, "ok": True, "mcp_source": "gke_remote_mcp", "args": {}}
        ]

    result = score_investigation_completeness(state, POLICY)
    assert result["components"]["required_evidence_coverage"] == 1.0, (
        f"real evidence_extractor output should satisfy OOMKilled's required domains; "
        f"got gaps={result['gaps']}"
    )
    assert result["band"] == "complete"


def test_resource_id_comes_from_real_call_args_not_llm_free_text(monkeypatch):
    """issue #206 regression: resource_id must be built from the real tool-call arguments,
    not trusted from the extractor LLM's own description -- even when the LLM's text is
    plausible-looking but doesn't literally contain the resolved namespace/pod, and even
    when the LLM's text is outright WRONG."""
    state = _state_with_tool_result("describe_k8s_resource")
    state["tool_history"] = [{
        "step": 0, "tool": "describe_k8s_resource", "ok": True, "mcp_source": "gke_remote_mcp",
        "args": {"namespace": "test-incidents", "name": "imagepull-pod",
                 "parent": "projects/p/locations/us-central1/clusters/c", "resourceType": "pod"},
    }]

    # The extractor LLM describes the SAME evidence in prose that never literally embeds
    # "test-incidents" or "imagepull-pod" -- the exact failure mode observed on a real run
    # (run_20260827_182635_yfcm), where this got the evidence scored a false mismatch.
    _mock_io(monkeypatch, {
        "resource_type": "pod",
        "resource_id": "the container with the bad image",
        "summary": "Container cannot pull its image", "key_facts": ["ImagePullBackOff"],
    })

    result = evidence_extractor_mod.evidence_extractor(state)
    ev_entry = result["evidence_store"]["ev_001"]

    assert ev_entry["resource_id"] == "test-incidents/imagepull-pod", (
        "resource_id must come from the real tool-call args, ignoring the LLM's own "
        f"(here misleading) description; got {ev_entry['resource_id']!r}"
    )


def test_resource_id_falls_back_to_resolved_context_when_call_has_no_explicit_target(monkeypatch):
    """A namespace-wide call (e.g. list_k8s_events with no `name`) carries no explicit
    target -- resource_id must fall back to the investigation's resolved namespace/pod,
    not go blank."""
    state = _state_with_tool_result("list_k8s_events")
    state["tool_history"] = [{
        "step": 0, "tool": "list_k8s_events", "ok": True, "mcp_source": "gke_remote_mcp",
        "args": {"namespace": "test-incidents",
                 "parent": "projects/p/locations/us-central1/clusters/c"},
    }]
    _mock_io(monkeypatch, {
        "resource_type": "pod", "resource_id": "irrelevant model text",
        "summary": "Events for the namespace", "key_facts": ["ImagePullBackOff event"],
    })

    result = evidence_extractor_mod.evidence_extractor(state)
    ev_entry = result["evidence_store"]["ev_001"]

    # state's resolved_context (from make_state) defaults pod="test-pod" — the fallback.
    assert ev_entry["resource_id"] == "test-incidents/test-pod"


def test_resource_id_uses_custom_mcp_name_field_for_the_called_tool(monkeypatch):
    """Custom k8s MCP tools use per-tool name-field keys (pod_name, deployment_name, ...),
    not gke_remote_mcp's uniform 'name' -- the deterministic builder must read the right
    one for whichever tool actually ran."""
    state = _state_with_tool_result("describe_pod_detail")
    state["latest_tool_result"]["mcp_source"] = "k8s_mcp"
    state["tool_history"] = [{
        "step": 0, "tool": "describe_pod_detail", "ok": True, "mcp_source": "k8s_mcp",
        "args": {"namespace": "test-incidents", "pod_name": "imagepull-pod"},
    }]
    _mock_io(monkeypatch, {
        "resource_type": "pod", "resource_id": "ignored",
        "summary": "Pod detail", "key_facts": ["ImagePullBackOff"],
    })

    result = evidence_extractor_mod.evidence_extractor(state)
    ev_entry = result["evidence_store"]["ev_001"]

    assert ev_entry["resource_id"] == "test-incidents/imagepull-pod"


def test_resource_type_comes_from_gke_remote_mcp_args_not_llm_free_text(monkeypatch):
    """2026-08-29 regression, same class as issue #206's resource_id fix: confirmed live
    during resource_identity_match validation -- evidence unambiguously about a ConfigMap
    (resource_id correctly showed "test-incidents/app-config") still had resource_type
    default to "pod" because the extractor LLM's own free-text output didn't name it,
    silently defeating the resource_identity_match relaxation that reads this field."""
    state = _state_with_tool_result("get_k8s_resource")
    state["tool_history"] = [{
        "step": 0, "tool": "get_k8s_resource", "ok": True, "mcp_source": "gke_remote_mcp",
        "args": {"namespace": "test-incidents", "name": "app-config",
                 "parent": "projects/p/locations/us-central1/clusters/c", "resourceType": "configmap"},
    }]
    # The extractor LLM's own output never mentions "configmap" at all -- exactly the real
    # failure mode observed live.
    _mock_io(monkeypatch, {
        "resource_type": "pod",  # wrong, as extracted by the LLM -- must be overridden
        "resource_id": "the missing config",
        "summary": "ConfigMap not found", "key_facts": ["NotFound"],
    })

    result = evidence_extractor_mod.evidence_extractor(state)
    ev_entry = result["evidence_store"]["ev_001"]

    assert ev_entry["resource_type"] == "configmap"


def test_resource_type_defaults_to_pod_when_gke_remote_call_has_no_resourcetype_arg(monkeypatch):
    state = _state_with_tool_result("list_k8s_events")
    state["tool_history"] = [{
        "step": 0, "tool": "list_k8s_events", "ok": True, "mcp_source": "gke_remote_mcp",
        "args": {"namespace": "test-incidents"},
    }]
    _mock_io(monkeypatch, {
        "resource_type": "pod", "resource_id": "irrelevant",
        "summary": "Events", "key_facts": ["Event"],
    })
    result = evidence_extractor_mod.evidence_extractor(state)
    assert result["evidence_store"]["ev_001"]["resource_type"] == "pod"


def test_resource_type_comes_from_custom_mcp_tool_name_not_llm_free_text(monkeypatch):
    """Custom K8s MCP tools encode the resource kind in the TOOL NAME itself
    (get_configmap, describe_deployment, ...) -- deterministic from the tool call, same
    "never trust the LLM's own free text" principle as the GKE Remote MCP case above."""
    state = _state_with_tool_result("describe_deployment")
    state["latest_tool_result"]["mcp_source"] = "k8s_mcp"
    state["tool_history"] = [{
        "step": 0, "tool": "describe_deployment", "ok": True, "mcp_source": "k8s_mcp",
        "args": {"namespace": "test-incidents", "deployment_name": "order-api"},
    }]
    _mock_io(monkeypatch, {
        "resource_type": "pod",  # wrong, as extracted by the LLM
        "resource_id": "ignored", "summary": "Deployment detail", "key_facts": ["Deployment ready"],
    })
    result = evidence_extractor_mod.evidence_extractor(state)
    assert result["evidence_store"]["ev_001"]["resource_type"] == "deployment"


def test_uninspected_marker_sets_fail_open_inspection_status(monkeypatch):
    """Section 8 (2026-09-08): mcp/response_guard.py injects "_uninspected": true into
    a tool result's own JSON payload when a Model Armor response check failed open --
    proves the real node detects it, strips it from what the LLM extractor/GCS-sanitized
    copy sees, and records it as an explicit inspection_status field."""
    state = _state_with_tool_result("get_pod_logs")
    state["latest_tool_result"]["result"] = {"status": "Running", "_uninspected": True}
    _mock_io(monkeypatch, {
        "resource_type": "pod", "resource_id": "irrelevant",
        "summary": "Pod status", "key_facts": ["Running"],
    })
    result = evidence_extractor_mod.evidence_extractor(state)
    ev = result["evidence_store"]["ev_001"]
    assert ev["inspection_status"] == "fail_open"


def test_no_uninspected_marker_sets_inspected_status(monkeypatch):
    state = _state_with_tool_result("get_pod_logs")
    _mock_io(monkeypatch, {
        "resource_type": "pod", "resource_id": "irrelevant",
        "summary": "Pod status", "key_facts": ["Running"],
    })
    result = evidence_extractor_mod.evidence_extractor(state)
    ev = result["evidence_store"]["ev_001"]
    assert ev["inspection_status"] == "inspected"
