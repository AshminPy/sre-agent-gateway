"""Regression test for issue #207: task_planner and mcp_router only ever saw the
normalized namespace/pod fields, never the original incident report -- when Pod
came back blank (e.g. the incident names a Service, not a pod), neither prompt had
any way to recover the actual target, and the investigation drifted onto whatever
looked loudest in the namespace instead.

Real repro: a live run against "notification-svc in test-incidents shows no
endpoints" (no pod hint) investigated an unrelated pod's missing ConfigMap instead,
scoring it 0.95 confidence. Fix: thread the original user_query into both prompts.
"""
import agent.nodes.mcp_router as mcp_router_mod
import agent.nodes.task_planner as task_planner_mod
from agent.nodes.mcp_router import mcp_router
from agent.nodes.task_planner import task_planner

USAGE = {
    "input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5,
    "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 15,
    "billable_output_tokens": 5, "cost_usd": 0.0,
    "provider": "gemini", "model": "gemini-2.5-pro", "duration_s": 0.1,
}

REAL_QUERY = "notification-svc in test-incidents shows no endpoints. Investigate why traffic is not reaching the pods."


def test_task_planner_prompt_includes_the_original_query_when_pod_is_blank(monkeypatch):
    captured = {}

    def _capture(system, user, **kwargs):
        captured["user"] = user
        return {"task_plan": "check service selector", "primary_gap": "endpoint count"}, USAGE

    monkeypatch.setattr(task_planner_mod, "llm_json", _capture)

    state = {
        "run_id": "run_test",
        "resolved_context": {
            "incident_type": "Unknown", "namespace": "test-incidents", "pod": "",
        },
        "investigation": {"current_step": 0, "evidence_gaps": []},
        "working_theory": "none yet",
        "incident_envelope": {"user_query": REAL_QUERY},
        "evidence_store": {},
    }
    task_planner(state)

    assert REAL_QUERY in captured["user"], (
        "task_planner must show the model the original incident report, especially "
        "when Pod is blank and it's the only place the real target name survives"
    )


def test_mcp_router_prompt_includes_the_original_query_when_pod_is_blank(monkeypatch):
    captured = {}

    def _capture(system, user, **kwargs):
        captured["user"] = user
        return {"tool": "done", "arguments": {}, "reason": "no gap left"}, USAGE

    registry = {"sre-test-cluster": {"cluster_type": "gke", "enabled": True}}
    monkeypatch.setattr(mcp_router_mod, "_get_cluster_registry", lambda: registry)
    monkeypatch.setattr(mcp_router_mod, "llm_json", _capture)

    state = {
        "run_id": "run_test",
        "resolved_context": {
            "cluster_name": "sre-test-cluster", "incident_type": "Unknown",
            "namespace": "test-incidents", "pod": "",
        },
        "investigation": {"current_step": 1, "task_plan": "check service selector",
                           "primary_gap": "endpoint count", "min_steps": 2},
        "sources_skipped": [],
        "evidence_ids": [],
        "tool_history": [],
        "incident_envelope": {"user_query": REAL_QUERY},
    }
    mcp_router(state)

    assert REAL_QUERY in captured["user"], (
        "mcp_router must show the model the original incident report so a blank Pod "
        "doesn't force an unscoped, namespace-wide tool call"
    )
