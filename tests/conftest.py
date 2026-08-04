"""Shared fixtures/builders for confidence-framework tests.

Builds plain dicts matching AgentState's shape (see agent/state.py) rather than importing the
TypedDict — the scorer functions only read via .get(), so a matching dict is sufficient and
keeps these tests independent of LangGraph/state.py internals.
"""
from __future__ import annotations

import time

CLUSTER = "sre-test-cluster"


def make_evidence(ev_id: str, tool: str, cluster: str = CLUSTER, summary: str = "",
                   key_facts=None, ok: bool = True) -> dict:
    return {
        "tool": tool,
        "cluster": cluster,
        "region": "us-central1",
        "summary": summary,
        "key_facts": key_facts or [],
        "raw_ref": f"gs://bucket/{ev_id}.json",
        "ok": ok,
    }


def make_tool_history_entry(step: int, tool: str, ok: bool = True, mcp_source: str = "gke_remote_mcp") -> dict:
    return {"step": step, "tool": tool, "ok": ok, "mcp_source": mcp_source, "args": {}}


def make_state(
    incident_type: str = "OOMKilled",
    evidence_store: dict | None = None,
    tool_history: list | None = None,
    current_step: int = 3,
    max_steps: int = 5,
    cluster_explicitly_provided: bool = True,
    selected_mcp: str = "gke_remote_mcp",
    started_at: float | None = None,
) -> dict:
    evidence_store = evidence_store or {}
    tool_history = tool_history or []
    return {
        "run_id": "run_test",
        "resolved_context": {
            "cluster_name": CLUSTER,
            "cluster_region": "us-central1",
            "project_id": "sreagent-test",
            "namespace": "test-incidents",
            "pod": "test-pod",
            "incident_type": incident_type,
            "mcp_source": selected_mcp,
            "cluster_explicitly_provided": cluster_explicitly_provided,
        },
        "investigation": {
            "current_step": current_step,
            "max_steps": max_steps,
            "min_steps": 2,
            "started_at": started_at if started_at is not None else time.time(),
        },
        "selected_mcp": selected_mcp,
        "evidence_ids": list(evidence_store.keys()),
        "evidence_store": evidence_store,
        "tool_history": tool_history,
    }
