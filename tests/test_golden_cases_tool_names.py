"""Regression guard for the 2026-08-09 stale-tool-name bug in agent/eval/golden_cases.py.

What happened: golden_cases.py's expected_trajectory arrays were written against an
older tool-naming scheme (list_pods, get_events, ...) that is CUSTOM_K8S_TOOLS-shaped,
while 12 of the 14 cases actually route through gke_remote_mcp and must use
GKE_REMOTE_TOOLS names instead. A plain "is this name in ALLOWED_TOOLS anywhere" check
would NOT have caught this — list_pods/list_events are valid CUSTOM_K8S_TOOLS names, so
they silently passed a membership-only check while still being wrong for a GKE-routed
case. This test checks membership in the CORRECT per-case tool set, not just "anywhere".

Source classification is intentionally a static per-case map, not a live cluster-registry
lookup — the golden dataset only ever exercises two cluster identities in this repo
(the default GKE cluster and one fixed on-prem/custom example), and resolving the real
routing decision here would require live GCS + Terraform state, which is exactly the I/O
boundary agent/eval/run_eval.py's local mode and tests/test_eval_scenario_matrix.py both
already treat as unavailable in this environment.
"""
from __future__ import annotations

from agent.eval.golden_cases import GOLDEN_CASES
from agent.mcp_client import CUSTOM_K8S_TOOLS, GKE_REMOTE_TOOLS

# Cases whose resource_hints.cluster routes through the custom K8s MCP (k8s_mcp),
# not gke_remote_mcp. Every other case with a non-empty expected_trajectory in this
# dataset targets the default GKE cluster (sre-test-cluster) and must use
# GKE_REMOTE_TOOLS names only.
CUSTOM_MCP_CASE_IDS = {"onprem-001"}


def test_every_case_is_classified():
    """Guards the classification map itself — a new case added to golden_cases.py
    with a cluster this test doesn't know about should fail loudly, not silently
    default to "GKE" and mask a future version of the same bug."""
    # sre-lab replaces the old fictional "onprem-dc1-cluster" (corrected 2026-09-08,
    # Section 10 / Phase 1 50-case manifest prep) -- sre-lab is the real, live
    # on-prem cluster this repo actually has registered.
    known_clusters = {"sre-test-cluster", "sre-lab"}
    for case in GOLDEN_CASES:
        cluster = case["payload"]["resource_hints"].get("cluster")
        if cluster is not None:
            assert cluster in known_clusters, (
                f"{case['id']}: unrecognized cluster {cluster!r} — add it to "
                f"test_golden_cases_tool_names.py's classification before trusting "
                f"this case's expected_trajectory tool names."
            )


def test_expected_trajectory_uses_correct_tool_scheme():
    for case in GOLDEN_CASES:
        allowed = CUSTOM_K8S_TOOLS if case["id"] in CUSTOM_MCP_CASE_IDS else GKE_REMOTE_TOOLS
        other = GKE_REMOTE_TOOLS if case["id"] in CUSTOM_MCP_CASE_IDS else CUSTOM_K8S_TOOLS
        for tool_name in case["expected_trajectory"]:
            assert tool_name in allowed, (
                f"{case['id']}: expected_trajectory tool {tool_name!r} is not valid for "
                f"its target MCP source. "
                f"{'Belongs to GKE_REMOTE_TOOLS, not CUSTOM_K8S_TOOLS.' if tool_name in other else 'Not a known tool name at all.'}"
            )
