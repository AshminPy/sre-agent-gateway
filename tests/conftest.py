"""Shared fixtures/builders for confidence-framework tests.

Builds plain dicts matching AgentState's shape (see agent/state.py) rather than importing the
TypedDict — the scorer functions only read via .get(), so a matching dict is sufficient and
keeps these tests independent of LangGraph/state.py internals.
"""
from __future__ import annotations

import time

CLUSTER = "sre-test-cluster"


def make_evidence(ev_id: str, tool: str, cluster: str = CLUSTER, summary: str = "",
                   key_facts=None, ok: bool = True, resource_id: str = "",
                   collected_at: float | None = None) -> dict:
    return {
        "tool": tool,
        "cluster": cluster,
        "region": "us-central1",
        "summary": summary,
        "key_facts": key_facts or [],
        "raw_ref": f"gs://bucket/{ev_id}.json",
        "ok": ok,
        "resource_id": resource_id,
        "collected_at": collected_at,
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


# ── Primary-claim verifier mocking (2026-09-01 confidence-architecture review) ─────────────
#
# The verifier (agent/confidence/verifier.py) has TWO separate I/O boundaries a test must
# mock, distinct from the RCA-generation call's own mocking:
#   1. Its LLM call: verify_primary_claim() does a per-call `from agent.llm import llm_json`
#      INSIDE the function body, not at module load time — this is a deliberate, separate
#      name binding from agent.nodes.rca_builder's own `from agent.llm import llm_json`
#      (bound once, at rca_builder.py's import time). Patching rca_builder_mod.llm_json
#      (the existing pattern every test here already uses for the RCA-generation call)
#      does NOT affect the verifier's call — patch agent.llm.llm_json itself instead, which
#      the verifier's fresh per-call import always re-resolves against.
#   2. Its evidence reads: verify_primary_claim() reads each cited/other evidence item's
#      raw_ref via agent.confidence.verifier's own `from agent.gcs_client import
#      read_evidence` (module-level, bound once) — patch
#      agent.confidence.verifier.read_evidence, same pattern as #1 but at import time
#      since that one IS a top-level import in verifier.py.
#
# Because these are two independently-bound names (not the same object as
# rca_builder_mod.llm_json), a test can mock the RCA-generation response and the verifier's
# response completely independently, with no need to branch on which prompt was sent.

DEFAULT_VERIFIER_USAGE = {
    "input_tokens": 40, "cached_input_tokens": 0, "output_tokens": 20,
    "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 60,
    "billable_output_tokens": 20, "cost_usd": 0.00002,
    "provider": "gemini", "model": "gemini-2.5-pro", "duration_s": 0.05,
}


def mock_verifier(
    monkeypatch,
    *,
    causal_assertion: str = "specific_cause",
    faithfulness: str = "supported",
    sufficiency: str = "sufficient",
    semantic_contradiction: str = "absent",
    temporal_relevance: str = "unknown",
    evidence_refs: list | None = None,
    contradiction_evidence_ref: str = "",
    rationale: str = "test verifier rationale",
    usage: dict | None = None,
    raise_exception: bool = False,
    return_unparseable: bool = False,
):
    """Mocks the verifier's LLM call to return a clean, valid categorical response by
    default (everything a real, well-supported specific cause would look like) — override
    only the fields a specific test cares about. Does NOT mock evidence reads; combine
    with mock_verifier_evidence() (below) to also control source_evidence_complete /
    contradiction_check_complete, or leave evidence reads unmocked to test the real
    "GCS unreachable in this environment" fail-closed path.
    """
    import agent.llm as agent_llm_mod

    if raise_exception:
        def _raise(*a, **k):
            raise RuntimeError("simulated verifier LLM failure")
        monkeypatch.setattr(agent_llm_mod, "llm_json", _raise)
        return

    if return_unparseable:
        # Matches agent.llm.llm_json_failed()'s real contract, confirmed by reading
        # agent/llm/base.py and agent/llm/gemini_adapter.py directly rather than
        # assuming a shape: a parse failure is a dict carrying
        # {LLM_JSON_PARSE_FAILED_KEY: <reason string>} (gemini_adapter.py:440-448) --
        # llm_json_failed() reads exactly that key (agent/llm/__init__.py:57-66) and
        # returns "" for anything else, including a bare {} (which is a legitimately
        # "no fields" parse, not a failure -- rca_builder.py handles that as no_evidence,
        # a separate path, not this one).
        from agent.llm.base import LLM_JSON_PARSE_FAILED_KEY
        from agent.llm import llm_json_failed as _real_failed_check
        fake_failure = {LLM_JSON_PARSE_FAILED_KEY: "simulated unparseable verifier response"}
        assert _real_failed_check(fake_failure), (
            "test assumption broken: llm_json_failed() no longer recognizes "
            "LLM_JSON_PARSE_FAILED_KEY -- update this mock to match its real current contract"
        )
        monkeypatch.setattr(
            agent_llm_mod, "llm_json", lambda *a, **k: (fake_failure, dict(usage or DEFAULT_VERIFIER_USAGE)),
        )
        return

    response = {
        "causal_assertion": causal_assertion,
        "faithfulness": faithfulness,
        "sufficiency": sufficiency,
        "semantic_contradiction": semantic_contradiction,
        "contradiction_evidence_ref": contradiction_evidence_ref,
        "temporal_relevance": temporal_relevance,
        "evidence_refs": evidence_refs if evidence_refs is not None else [],
        "rationale": rationale,
    }
    monkeypatch.setattr(
        agent_llm_mod, "llm_json",
        lambda *a, **k: (dict(response), dict(usage or DEFAULT_VERIFIER_USAGE)),
    )


def mock_verifier_evidence(monkeypatch, available_ids: set | list | None = None, oversized_ids: set | list | None = None):
    """Mocks agent.confidence.verifier's read_evidence() so cited/other evidence items
    resolve to a small, real-looking sanitized payload (source_evidence_complete=True /
    contradiction_check_complete=True) for IDs in `available_ids`, and to "too large" /
    "unreadable" for everything else. Pass available_ids=None (default) to make every ID
    resolve successfully — the common case. Pass oversized_ids to simulate a payload that
    exists but exceeds the verifier's truncation budget (source/contradiction incomplete,
    never silently truncated-and-trusted)."""
    import agent.confidence.verifier as verifier_mod

    available = set(available_ids) if available_ids is not None else None
    oversized = set(oversized_ids or [])

    def _fake_read_evidence(raw_ref: str):
        # raw_ref shape from tests/conftest.py's make_evidence: gs://bucket/{ev_id}.json
        ev_id = raw_ref.rsplit("/", 1)[-1].removesuffix(".json")
        if ev_id in oversized:
            return {"sanitized": {"note": "x" * (verifier_mod._MAX_RAW_CHARS_PER_ITEM + 1)}}
        if available is None or ev_id in available:
            return {"sanitized": {"detail": f"real evidence content for {ev_id}"}}
        return None

    monkeypatch.setattr(verifier_mod, "read_evidence", _fake_read_evidence)
