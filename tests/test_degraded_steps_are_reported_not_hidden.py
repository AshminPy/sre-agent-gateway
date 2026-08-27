"""Regression tests: a degraded step must be reported, never dressed up as success.

Second review pass, 2026-08-27. The first pass (see
test_failed_results_never_become_evidence.py) covered how tool output becomes
evidence. This pass covers the REST of the workflow -- planning, routing,
extraction, memory recall, and the outer error boundary.

Same underlying bug class throughout: a step fails, the code substitutes a
plausible-looking default, and nothing downstream can tell the difference.

  Gap 6  Memory Bank recall returned "" for THREE different situations --
         not configured, genuinely nothing found, and recall threw. The report
         rendered all three as the positive claim "No prior similar incidents
         found in Memory Bank", and the LLM was told "No past investigations on
         record." Two of the three make that a false statement.

  Gap 7  mcp_router collapsed "the model chose done", "the response was
         unparseable", and "the response had no tool key" into one branch,
         logged at INFO as "mcp_router → done", all producing
         loop_exit_reason="tool_signaled_done". Two of the three are failures
         that silently ended the investigation while the report claimed a
         normal, complete run.

  Gap 8  task_planner substituted a generic plan/gap on an unparseable model
         response and logged it at INFO as though planning had worked.
         primary_gap then drives the router's next tool choice.

  Gap 9  evidence_extractor wrote its fallback entry with ok=True. An ok=True
         entry with no key_facts is indistinguishable from a real extraction and
         INFLATES scoring -- measured on a real ImagePullBackOff shape:
         required_evidence_coverage 0.0 -> 0.5, completeness 0.35 -> 0.55, and a
         genuinely missing evidence domain disappeared from the reported gaps.

  Gap 10 The outer error boundary returned only {"error", "status"} -- no
         run_id. Reproducing a crash meant hunting Cloud Logging by timestamp.
"""
import time


from agent.confidence import POLICY
from agent.confidence.scorer import score_investigation_completeness
from agent.llm.base import LLM_JSON_PARSE_FAILED_KEY
from agent.main import MEMORY_RECALL_UNAVAILABLE


# ── Gap 6: three memory states must produce three different statements ───────

def _memory_note(memory_ctx: str) -> str:
    """Mirrors the branch in agent/main.py's report renderer."""
    if memory_ctx == MEMORY_RECALL_UNAVAILABLE:
        return (
            "UNKNOWN — Memory Bank could not be reached, so prior incidents were "
            "NOT checked (this is not the same as 'none found')"
        )
    if memory_ctx:
        return f"Prior context recalled ({len(memory_ctx)} chars) — agent used past investigations"
    return "No prior similar incidents found in Memory Bank"


def test_memory_recall_failure_does_not_claim_no_incidents_found():
    note = _memory_note(MEMORY_RECALL_UNAVAILABLE)
    assert "UNKNOWN" in note
    assert "NOT checked" in note
    # The exact false claim this gap produced.
    assert "No prior similar incidents found" not in note


def test_memory_recall_genuine_empty_still_reports_none_found():
    """The one case where asserting 'none found' is actually true."""
    assert _memory_note("") == "No prior similar incidents found in Memory Bank"


def test_memory_recall_with_content_reports_recall():
    assert "Prior context recalled" in _memory_note("Past incidents on this cluster:\n  x")


def test_unavailable_sentinel_is_distinct_from_empty():
    """The whole fix depends on these two never being equal."""
    assert MEMORY_RECALL_UNAVAILABLE != ""
    assert bool(MEMORY_RECALL_UNAVAILABLE) is True


# ── Gap 7: router failure must not look like a normal completion ─────────────

def _router_outcome(action: dict) -> str:
    """Mirrors the branch in agent/nodes/mcp_router.py."""
    from agent.llm import llm_json_failed
    tool = action.get("tool", "") if action else ""
    router_failure = llm_json_failed(action)
    if router_failure or (action and not tool):
        return "failed"
    if not action or not tool or tool == "done":
        return "done"
    return tool


def test_router_parse_failure_is_a_failure_not_a_completion():
    action = {LLM_JSON_PARSE_FAILED_KEY: "no JSON object in model response"}
    assert _router_outcome(action) == "failed"


def test_router_response_missing_tool_key_is_a_failure():
    assert _router_outcome({"arguments": {"namespace": "x"}}) == "failed"


def test_router_genuine_done_is_still_a_normal_completion():
    """Guard: the real 'investigation complete' path must be untouched."""
    assert _router_outcome({"tool": "done"}) == "done"


def test_router_real_tool_choice_is_unaffected():
    assert _router_outcome({"tool": "describe_k8s_resource", "arguments": {}}) == "describe_k8s_resource"


# ── Gap 9: a failed extraction must not inflate the scores ──────────────────

def _completeness(evidence_ok: bool) -> dict:
    now = time.time()
    store = {
        "ev_001": {
            "ok": evidence_ok,
            "tool": "describe_k8s_resource",
            "step": 1,
            "summary": "EXTRACTION FAILED for describe_k8s_resource",
            "key_facts": [],
            "collected_at": now,
        }
    }
    state = {
        "evidence_store": store,
        "evidence_ids": list(store),
        "tool_history": [{"tool": "describe_k8s_resource", "ok": True, "step": 1}],
        "investigation": {"current_step": 1, "max_steps": 5, "started_at": now,
                          "incident_type": "ImagePullBackOff"},
        "resolved_context": {"incident_type": "ImagePullBackOff"},
        "selected_mcp_source": "gke_remote_mcp",
    }
    return score_investigation_completeness(state, POLICY)


def test_failed_extraction_does_not_count_as_evidence_coverage():
    """The regression: written as ok=True, this scored 0.5 coverage for nothing."""
    result = _completeness(evidence_ok=False)
    assert result["components"]["required_evidence_coverage"] == 0.0


def test_failed_extraction_leaves_the_missing_domain_visible():
    """Marking it ok=True hid kubernetes_status from the reported gaps."""
    result = _completeness(evidence_ok=False)
    missing = " ".join(result["gaps"])
    assert "kubernetes_events" in missing
    assert "kubernetes_status" in missing


def test_the_old_ok_true_behaviour_would_have_inflated_coverage():
    """Documents the exact inflation, so the fix is never quietly reverted."""
    inflated = _completeness(evidence_ok=True)["components"]["required_evidence_coverage"]
    honest = _completeness(evidence_ok=False)["components"]["required_evidence_coverage"]
    assert inflated > honest


# ── Gap 10: a crash must carry correlation handles ──────────────────────────

def test_error_boundary_returns_correlation_handles(monkeypatch):
    """A crashed run must be findable without a Cloud Logging timestamp hunt."""
    import agent.main as main_mod

    def _boom(*a, **k):
        raise RuntimeError("simulated graph explosion")

    monkeypatch.setattr(main_mod, "_get_graph", _boom)
    result = main_mod.investigate({
        "query": "Pod imagepull-pod in test-incidents cannot pull its image",
        "cluster": "sre-test-cluster",
    })

    assert result["status"] == "failed"
    assert result["error_type"] == "RuntimeError"
    assert "simulated graph explosion" in result["error"]
    # The handles that were missing entirely before.
    assert "run_id" in result
    assert result["cluster"] == "sre-test-cluster"
    assert "duration_s" in result


def test_error_boundary_never_raises_a_second_error(monkeypatch):
    """The error path must survive a crash that happens before `state` exists."""
    import agent.main as main_mod

    def _boom_early(*a, **k):
        raise ValueError("crashed before state was assigned")

    # get_initial_state is imported inside investigate(), so patch it at source.
    import agent.state as state_mod
    monkeypatch.setattr(state_mod, "get_initial_state", _boom_early)

    result = main_mod.investigate({"query": "Pod x in ns y cannot pull its image"})
    assert result["status"] == "failed"
    assert result["run_id"] == ""          # honestly empty, not a crash
    assert result["error_type"] == "ValueError"
