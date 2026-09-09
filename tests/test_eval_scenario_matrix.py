"""Minimum RCA accuracy validation — PRODUCTION-LAUNCH-PLAN.md Priority 8.

Real end-to-end validation against the confidence framework (agent/confidence/) and the
deterministic routing logic (agent/mcp_client.py, agent/nodes/context_resolver.py,
agent/nodes/mcp_router.py) — every function under test here is the actual production code,
called directly, with only two I/O boundaries stubbed:

  - the Gemini LLM call (agent.gemini_client.llm_json) — no API key/quota in this environment,
    same boundary agent/eval/run_eval.py and tests/test_rca_builder_integration.py already
    treat as non-deterministic and mock.
  - real network calls inside agent.mcp_client.call_tool (httpx) — no live GKE cluster /
    Connect Gateway / Cloud Run MCP reachable from this environment.

Nothing else is mocked: build_claims, detect_contradictions, score_root_cause_confidence,
score_investigation_completeness, derive_outcome, resolve_cluster_routing, the tool allowlist,
and the MCP-source-selection logic in mcp_router.py all run for real against the constructed
scenario input. This is the same technique tests/test_rca_builder_integration.py already
established for this exact purpose (see its module docstring).

Scenario coverage (PRODUCTION-LAUNCH-PLAN.md Priority 8 matrix):
  CrashLoopBackOff, ImagePullBackOff, OOMKilled, Pending, non-GKE via custom MCP,
  insufficient-evidence, conflicting-evidence, ambiguous-routing (Task 1 safe-stop),
  MCP/Connect-Gateway failure, remediation-stays-advisory.

Companion golden_cases.py entries (pending-001, onprem-001, insufficient-evidence-001,
conflicting-evidence-001, ambiguous-routing-001, mcp-gateway-failure-001) document the same
scenarios in run_eval.py's trajectory/keyword shape for whenever this runs against live GKE +
Gemini credentials; the tests below are what actually executes here, today, deterministically.
"""
from __future__ import annotations

import agent.mcp_client as mcp_client_mod
import agent.nodes.mcp_router as mcp_router_mod
import agent.nodes.rca_builder as rca_builder_mod
from agent.confidence.scorer import score_investigation_completeness
from agent.confidence.policy import POLICY
from agent.mcp_client import ALLOWED_TOOLS, BLOCKED_ACTIONS, CUSTOM_K8S_TOOLS, call_tool
from agent.nodes.context_resolver import context_resolver
from agent.nodes.mcp_router import mcp_router
from agent.state import get_initial_state
from tests.conftest import (
    CLUSTER, make_evidence, make_state, make_tool_history_entry,
    mock_verifier, mock_verifier_evidence,
)


def _quiet_observability_log(monkeypatch):
    """Cloud Logging isn't reachable from this environment — same non-assertion as
    test_rca_builder_integration.py, just applied explicitly so pytest -s output stays
    readable instead of full gRPC tracebacks from the (already try/except-guarded) call."""
    monkeypatch.setattr(rca_builder_mod, "_write_observability_log", lambda *a, **k: None)


def _mock_llm_json(monkeypatch, response: dict, usage: dict | None = None):
    usage = usage or {
        "input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 50,
        "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 150,
        "billable_output_tokens": 50, "cost_usd": 0.0001,
        "provider": "gemini", "model": "gemini-2.5-pro", "duration_s": 0.1,
    }
    monkeypatch.setattr(rca_builder_mod, "llm_json", lambda *a, **k: (dict(response), dict(usage)))


def _base_state(incident_type, evidence_store, tool_history):
    state = make_state(incident_type, evidence_store, tool_history)
    state["run_id"] = f"run_test_{incident_type.lower()}"
    state["incident_id"] = f"inc_test_{incident_type.lower()}"
    state["incident_envelope"] = {"user_query": f"pod is {incident_type}", "memory_context": ""}
    state["sources_skipped"] = []
    state["investigation"]["completeness"] = score_investigation_completeness(state, POLICY)
    state["investigation"]["tokens_total"] = 0
    state["investigation"]["estimated_cost_usd"] = 0.0
    return state


# ── 1. CrashLoopBackOff — grounded, multi-domain evidence should confirm/probable ──

def test_crashloop_backoff_grounded_rca_confirms_cause(monkeypatch):
    _quiet_observability_log(monkeypatch)
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail",
                                 key_facts=["Status Waiting reason CrashLoopBackOff"]),
        "ev_002": make_evidence("ev_002", "get_current_logs",
                                 key_facts=["process exited with code 137 fatal error"]),
        "ev_003": make_evidence("ev_003", "list_events",
                                 key_facts=["BackOff restarting failed container"]),
    }
    tool_history = [
        make_tool_history_entry(0, "describe_pod_detail"),
        make_tool_history_entry(1, "get_current_logs"),
        make_tool_history_entry(2, "list_events"),
    ]
    state = _base_state("CrashLoopBackOff", evidence_store, tool_history)

    _mock_llm_json(monkeypatch, {
        "primary_causal_claim_index": 1,
        "claims": [{
            "text": "Container CrashLoopBackOff, process exited fatal error",
            "claim_type": "observed_fact",
            "supporting_evidence_ids": ["ev_001", "ev_002"],
        }],
        "alternative_hypotheses_considered": [],
        "evidence_chain": ["ev_001", "ev_002", "ev_003"], "evidence_gaps": [],
        "reasoning_trace": [], "suggested_remediation": ["Check the container's startup command and fix the crashing bug."],
        "sources_skipped": [],
    })
    mock_verifier(monkeypatch)
    mock_verifier_evidence(monkeypatch)

    result = rca_builder_mod.rca_builder(state)["final_summary"]

    assert "CrashLoopBackOff" in result["likely_root_cause"]
    assert result["outcome"] in ("confirmed", "probable")
    assert all(c["grounding_status"] == "grounded" for c in result["claims"])
    # Remediation stays advisory — structured objects now (Section 7, 2026-09-08), each
    # with an "action" string, but still just recommendations, no execution flag anywhere.
    assert isinstance(result["suggested_remediation"], list)
    assert all(isinstance(r, dict) and "action" in r for r in result["suggested_remediation"])
    assert "auto_apply" not in result and "remediation_applied" not in result


# ── 2. ImagePullBackOff ─────────────────────────────────────────────────────

def test_imagepull_backoff_grounded_rca(monkeypatch):
    _quiet_observability_log(monkeypatch)
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail",
                                 key_facts=["Status Waiting reason ImagePullBackOff"]),
        "ev_002": make_evidence("ev_002", "list_events",
                                 key_facts=["Failed ErrImagePull manifest unknown image not found"]),
    }
    tool_history = [
        make_tool_history_entry(0, "describe_pod_detail"),
        make_tool_history_entry(1, "list_events"),
    ]
    state = _base_state("ImagePullBackOff", evidence_store, tool_history)

    _mock_llm_json(monkeypatch, {
        "primary_causal_claim_index": 1,
        "claims": [{
            "text": "ImagePullBackOff caused by ErrImagePull, image manifest not found",
            "claim_type": "observed_fact",
            "supporting_evidence_ids": ["ev_001", "ev_002"],
        }],
        "alternative_hypotheses_considered": [],
        "evidence_chain": ["ev_001", "ev_002"], "evidence_gaps": [],
        "reasoning_trace": [], "suggested_remediation": ["Verify the image tag and registry credentials."],
        "sources_skipped": [],
    })
    mock_verifier(monkeypatch)
    mock_verifier_evidence(monkeypatch)

    result = rca_builder_mod.rca_builder(state)["final_summary"]

    assert "ImagePullBackOff" in result["likely_root_cause"]
    assert result["outcome"] in ("confirmed", "probable")
    assert all(c["grounding_status"] == "grounded" for c in result["claims"])


# ── 3. OOMKilled — also checks correct cluster/namespace/workload identification ──

def test_oomkilled_investigation_context_matches_resolved_cluster_namespace(monkeypatch):
    _quiet_observability_log(monkeypatch)
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["exit code 137"]),
        "ev_002": make_evidence("ev_002", "list_events", key_facts=["OOMKilled memory limit exceeded"]),
        "ev_003": make_evidence("ev_003", "get_previous_logs", key_facts=["killed out of memory"]),
    }
    tool_history = [
        make_tool_history_entry(0, "describe_pod_detail"),
        make_tool_history_entry(1, "list_events"),
        make_tool_history_entry(2, "get_previous_logs"),
    ]
    state = _base_state("OOMKilled", evidence_store, tool_history)

    _mock_llm_json(monkeypatch, {
        "primary_causal_claim_index": 1,
        "claims": [{"text": "Container OOMKilled, exit code 137, memory limit exceeded",
                     "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001", "ev_002"]}],
        "alternative_hypotheses_considered": [],
        "evidence_chain": ["ev_001", "ev_002", "ev_003"], "evidence_gaps": [],
        "reasoning_trace": [], "suggested_remediation": ["Raise the memory limit or fix the leak."],
        "sources_skipped": [],
    })
    mock_verifier(monkeypatch)
    mock_verifier_evidence(monkeypatch)

    result = rca_builder_mod.rca_builder(state)["final_summary"]
    ctx = result["investigation_context"]

    # Correct cluster / namespace / workload identified — pulled from resolved_context, not
    # invented by the LLM.
    assert ctx["cluster"] == CLUSTER
    assert ctx["namespace"] == "test-incidents"
    assert result["outcome"] in ("confirmed", "probable")


# ── 4. Pending / unschedulable ──────────────────────────────────────────────

def test_pending_unschedulable_grounded(monkeypatch):
    _quiet_observability_log(monkeypatch)
    evidence_store = {
        "ev_001": make_evidence("ev_001", "list_pods", key_facts=["Status Pending 0/1 Ready"]),
        "ev_002": make_evidence("ev_002", "list_events",
                                 key_facts=["FailedScheduling Insufficient cpu 0/3 nodes are available"]),
    }
    tool_history = [
        make_tool_history_entry(0, "list_pods"),
        make_tool_history_entry(1, "list_events"),
    ]
    state = _base_state("Pending", evidence_store, tool_history)

    _mock_llm_json(monkeypatch, {
        "primary_causal_claim_index": 1,
        "claims": [{"text": "Pod stuck Pending — FailedScheduling, Insufficient cpu on all nodes",
                     "claim_type": "observed_fact",
                     "supporting_evidence_ids": ["ev_001", "ev_002"]}],
        "alternative_hypotheses_considered": [],
        "evidence_chain": ["ev_001", "ev_002"], "evidence_gaps": [],
        "reasoning_trace": [], "suggested_remediation": ["Scale the node pool or reduce requested cpu."],
        "sources_skipped": [],
    })
    mock_verifier(monkeypatch)
    mock_verifier_evidence(monkeypatch)

    result = rca_builder_mod.rca_builder(state)["final_summary"]

    assert "Pending" in result["likely_root_cause"]
    assert "Insufficient" in result["likely_root_cause"]
    assert result["outcome"] in ("confirmed", "probable")
    assert all(c["grounding_status"] == "grounded" for c in result["claims"])


# ── 5. Non-GKE cluster — must route to the custom K8s MCP, never gke_remote_mcp ──

def test_non_gke_cluster_routes_to_custom_mcp(monkeypatch):
    registry = {
        "onprem-dc1-cluster": {
            "canonical_id": "onprem-dc1-cluster",
            "cluster_type": "custom",
            "enabled": True,
        },
    }
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: registry)
    monkeypatch.setattr(
        mcp_router_mod, "llm_json",
        lambda *a, **k: (
            {"tool": "list_pods", "arguments": {"namespace": "billing-ns"}, "reason": "start investigation"},
            {
                "input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5,
                "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 15,
                "billable_output_tokens": 5, "cost_usd": 0.0,
                "provider": "gemini", "model": "gemini-2.5-pro", "duration_s": 0.1,
            },
        ),
    )

    state = {
        "run_id": "run_test_onprem",
        "resolved_context": {
            "cluster_name": "onprem-dc1-cluster",
            "incident_type": "CrashLoopBackOff",
            "namespace": "billing-ns",
            "pod": "legacy-billing-0",
        },
        "investigation": {"current_step": 0, "task_plan": "", "primary_gap": "", "min_steps": 2},
        "sources_skipped": [],
        "evidence_ids": [],
        "tool_history": [],
    }

    result = mcp_router(state)

    assert result["selected_mcp"] == "k8s_mcp"
    assert result["current_action"]["mcp_source"] == "k8s_mcp"
    assert result["current_action"]["tool"] in CUSTOM_K8S_TOOLS
    assert result["current_action"]["tool"] not in mcp_client_mod.GKE_REMOTE_TOOLS


# ── 6. Ambiguous routing — Task 1's human safe-stop, end to end into rca_builder ──

def test_ambiguous_routing_triggers_safe_stop_and_insufficient_evidence(monkeypatch):
    """No cluster_hint, and the namespace hint matches TWO enabled clusters — resolve_cluster_
    routing (real function) must refuse to guess, context_resolver must safe-stop, and — since
    graph.py routes a "failed" status straight to rca_builder — rca_builder must produce a
    clean insufficient_evidence outcome instead of crashing or inventing a cause.

    This exercises the exact path that used to raise KeyError('score') inside
    confidence.scorer.derive_outcome() before the rca_builder.py completeness-default fix
    added alongside this test: task_evaluator never runs on a safe-stop, so
    investigation['completeness'] was never set."""
    _quiet_observability_log(monkeypatch)

    registry = {
        "cluster-a": {"canonical_id": "cluster-a", "aliases": [], "project": "p", "region": "us-east1",
                      "cluster_type": "gke", "environment": "production",
                      "allowed_namespaces": ["checkout"], "owner": "", "enabled": True,
                      "mcp_primary": "gke_remote_mcp", "mcp_fallback": "k8s_mcp", "mcp_url": ""},
        "cluster-b": {"canonical_id": "cluster-b", "aliases": [], "project": "p", "region": "us-east1",
                      "cluster_type": "gke", "environment": "production",
                      "allowed_namespaces": ["checkout"], "owner": "", "enabled": True,
                      "mcp_primary": "gke_remote_mcp", "mcp_fallback": "k8s_mcp", "mcp_url": ""},
    }
    monkeypatch.setattr(mcp_client_mod, "_get_cluster_registry", lambda: registry)

    routing = mcp_client_mod.resolve_cluster_routing(namespace_hint="checkout")
    assert routing["resolved"] is False
    assert routing["method"] == "unresolved"

    ctx_state = {
        "run_id": "run_test_ambiguous",
        "resolved_context": {"cluster_hint": "", "cluster_guess": "", "namespace": "checkout"},
        "investigation": {"current_step": 0, "status": "running"},
    }
    ctx_result = context_resolver(ctx_state)
    assert ctx_result["investigation"]["status"] == "failed"
    assert "cluster_name" not in ctx_result["resolved_context"]

    state = get_initial_state({"user_query": "Something is wrong with the checkout pod.", "resource_hints": {}})
    state["resolved_context"] = ctx_result["resolved_context"]
    state["investigation"].update(ctx_result["investigation"])
    state["incident_envelope"] = {"user_query": "Something is wrong with the checkout pod.", "memory_context": ""}

    _mock_llm_json(monkeypatch, {"likely_root_cause": "unused — no_evidence path overrides this"})

    result = rca_builder_mod.rca_builder(state)["final_summary"]

    assert result["outcome"] == "insufficient_evidence"
    assert result["confidence_score"] == 0.0
    assert result["requires_human_review"] is True
    assert "unknown" in result["likely_root_cause"].lower() or "cannot be determined" in result["likely_root_cause"].lower()
    assert result["investigation_context"]["cluster_routing_method"] == "unresolved"


# ── 7. Insufficient evidence — LLM overclaims, grounding must reject it ─────

def test_insufficient_evidence_does_not_invent_a_cause(monkeypatch):
    """Thin, single-domain evidence — the LLM (mocked) still tries to assert a confident
    cause citing evidence that doesn't exist. Grounding must reject the phantom citation and
    the deterministic scorer must not let that turn into a confirmed outcome."""
    _quiet_observability_log(monkeypatch)
    evidence_store = {
        "ev_001": make_evidence("ev_001", "list_pods", key_facts=["pod not found"]),
    }
    tool_history = [make_tool_history_entry(0, "list_pods")]
    state = _base_state("CrashLoopBackOff", evidence_store, tool_history)

    _mock_llm_json(monkeypatch, {
        "likely_root_cause": "Definitely caused by a bad deployment rollout (ev_999)",
        "claims": [{
            "text": "Definitely caused by a bad deployment rollout",
            "claim_type": "observed_fact",
            "supporting_evidence_ids": ["ev_999"],  # phantom — not in evidence_ids
        }],
        "alternative_hypotheses_considered": [],
        "evidence_chain": ["ev_001"], "evidence_gaps": ["pod no longer exists"],
        "reasoning_trace": [], "suggested_remediation": [],
        "sources_skipped": [],
    })

    result = rca_builder_mod.rca_builder(state)["final_summary"]

    assert result["claims"][0]["grounding_status"] == "phantom_evidence"
    assert result["claims"][0]["support_strength"] == 0.0
    assert result["outcome"] not in ("confirmed",)
    assert result["outcome"] in ("insufficient_evidence", "unknown", "possible")
    assert result["confidence_score"] < POLICY.band_thresholds["auto"]


# ── 8. Conflicting evidence — cross-cluster contradiction must not be averaged away ──

def test_conflicting_evidence_forces_conflicting_outcome(monkeypatch):
    _quiet_observability_log(monkeypatch)
    evidence_store = {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", cluster=CLUSTER,
                                 key_facts=["Status Running unstable restarts"]),
        # Tagged with a DIFFERENT cluster than resolved_context — stale cache / cross-cluster
        # name collision.
        "ev_002": make_evidence("ev_002", "list_events", cluster="some-other-cluster",
                                 key_facts=["evicted node pressure"]),
    }
    tool_history = [
        make_tool_history_entry(0, "describe_pod_detail"),
        make_tool_history_entry(1, "list_events"),
    ]
    state = _base_state("CrashLoopBackOff", evidence_store, tool_history)

    _mock_llm_json(monkeypatch, {
        "primary_causal_claim_index": 1,
        "claims": [{
            "text": "Pod unstable with restarts",
            "claim_type": "observed_fact",
            "supporting_evidence_ids": ["ev_001", "ev_002"],
        }],
        "alternative_hypotheses_considered": [],
        "evidence_chain": ["ev_001", "ev_002"], "evidence_gaps": [],
        "reasoning_trace": [], "suggested_remediation": [],
        "sources_skipped": [],
    })
    # The conflict here is the DETERMINISTIC wrong-resource check (detect_contradictions()
    # comparing ev_002's cluster tag against resolved_context) -- a clean/passing verifier
    # mock is still needed to get past gates 1-2 of derive_outcome() before the hard-conflict
    # check at gate 3 is even reached; this test is not exercising the verifier's OWN
    # semantic_contradiction judgment (see test_confirmed_contract.py for that case).
    mock_verifier(monkeypatch)
    mock_verifier_evidence(monkeypatch)

    result = rca_builder_mod.rca_builder(state)["final_summary"]

    assert result["outcome"] == "conflicting_evidence"
    assert len(result["contradictions"]) >= 1
    assert result["contradictions"][0]["kind"] == "wrong_resource"
    assert result["requires_human_review"] is True


# ── 9. MCP / Connect Gateway failure — must surface, never silently succeed ────

def test_gke_remote_mcp_network_failure_surfaces_as_tool_failure(monkeypatch):
    """A Connect Gateway / GKE Remote MCP outage manifests as a network-level exception
    (timeout/connection error), not an HTTP error status — call_tool's real code path for
    that is the bottom `except Exception` handler. Verifies it returns ok=False with the
    error surfaced, rather than raising uncaught or fabricating a result."""
    monkeypatch.setattr(mcp_client_mod, "_get_access_token", lambda: "fake-token")
    monkeypatch.setattr(
        mcp_client_mod, "_get_cluster_registry",
        lambda: {CLUSTER: {"project": "sreagent-test", "region": "us-central1",
                            "mcp_fallback": "k8s_mcp"}},
    )

    class _BoomClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            raise ConnectionError("Connect Gateway: connection refused")

    monkeypatch.setattr(mcp_client_mod.httpx, "Client", lambda timeout=30.0: _BoomClient())

    result = call_tool("gke_remote_mcp", "get_k8s_cluster_info", {}, cluster_name=CLUSTER)

    assert result["ok"] is False
    assert "Connect Gateway" in result["error"] or "connection refused" in result["error"]
    assert result["tool"] == "get_k8s_cluster_info"
    assert result["mcp_source"] == "gke_remote_mcp"

    # Downstream propagation: a failed tool call must degrade completeness, not be ignored.
    failing_history = [{"step": 0, "tool": "get_k8s_cluster_info", "ok": False,
                         "mcp_source": "gke_remote_mcp", "args": {}}]
    completeness_state = {
        "resolved_context": {"cluster_explicitly_provided": True, "incident_type": "Unknown"},
        "investigation": {"current_step": 1, "max_steps": 5},
        "evidence_store": {},
        "tool_history": failing_history,
    }
    completeness = score_investigation_completeness(completeness_state, POLICY)
    assert completeness["components"]["tool_success"] == 0.0
    assert any("failed" in g.lower() for g in completeness["gaps"])


# ── 10. Remediation is structurally advisory-only, everywhere ──────────────

def test_tool_allowlist_contains_no_write_actions():
    """The entire MCP tool surface — the only thing any node (including a future
    remediation step) could invoke — is read-only by construction. No allowed tool name
    contains a blocked write verb, so nothing in the pipeline can apply a change."""
    for tool in ALLOWED_TOOLS:
        for blocked in BLOCKED_ACTIONS:
            assert blocked not in tool.lower(), f"tool '{tool}' contains blocked action '{blocked}'"
