"""Regression test for the resource_identity_match fix, 2026-08-29.

Root-caused in docs/management/confidence-genericity-review-2026-08-28.md #15.7 against two
real live runs (configmap-001, init-001, both 2026-08-28, after PR #206's resource_id fix was
already deployed): a ConfigMap or Service the pod genuinely depends on can never contain the
pod's own name in its resource_id ("namespace/name" of the ConfigMap/Service itself, not the
pod), so the old pod-name containment check falsely dinged the RCA's own key causal evidence.

Fixtures below reconstruct the two real cases' evidence/claims/resolved_context exactly as
pulled from gs://sreagent-t2-demo-eval/runs/run_20260828_154426_rmlk.json (configmap-001) and
run_20260828_154700_rtrb.json (init-001) during that root-cause investigation, confirmed to
reproduce the real live scores (0.85 and 0.84) before this fix.
"""
from __future__ import annotations

from agent.confidence.claim_builder import build_claims
from agent.confidence.policy import POLICY
from agent.confidence.scorer import score_root_cause_confidence


def _configmap_001_fixture():
    evidence_store = {
        "ev_001": {
            "ok": True, "tool": "list_k8s_events", "mcp_source": "gke_remote_mcp",
            "cluster": "sre-test-cluster", "resource_type": "pod",
            "resource_id": "test-incidents/auth-service",
            "summary": "Event: FailedMount configmap 'app-config' not found",
            "key_facts": ["MountVolume.SetUp failed for volume config-volume: configmap app-config not found"],
        },
        "ev_003": {
            "ok": True, "tool": "get_k8s_resource", "mcp_source": "gke_remote_mcp",
            "cluster": "sre-test-cluster", "resource_type": "configmap",
            "resource_id": "test-incidents/app-config",
            "summary": "Error from server (NotFound): ConfigMap \"app-config\" not found",
            "key_facts": ["ConfigMap app-config NotFound"],
        },
    }
    evidence_ids = list(evidence_store.keys())
    resolved_context = {
        "cluster_explicitly_provided": True, "cluster_name": "sre-test-cluster",
        "namespace": "test-incidents", "pod": "auth-service", "mcp_source": "gke_remote_mcp",
    }
    rca_result = {
        "claims": [
            {
                "claim_type": "observed_fact",
                "text": "A direct attempt to retrieve the ConfigMap 'app-config' from the "
                        "cluster failed with a 'NotFound' error.",
                "supporting_evidence_ids": ["ev_003"],
            },
        ],
    }
    claims = build_claims(rca_result, evidence_ids, evidence_store)
    tool_history = [
        {"step": 1, "tool": "list_k8s_events", "mcp_source": "gke_remote_mcp", "ok": True},
        {"step": 2, "tool": "get_k8s_resource", "mcp_source": "gke_remote_mcp", "ok": True},
    ]
    return claims, evidence_store, tool_history, resolved_context


def _init_001_fixture():
    evidence_store = {
        "ev_003": {
            "ok": True, "tool": "get_k8s_resource", "mcp_source": "gke_remote_mcp",
            "cluster": "sre-test-cluster", "resource_type": "service",
            "resource_id": "test-incidents/db-service",
            "summary": "Error from server (NotFound): Service \"db-service\" not found",
            "key_facts": ["Service db-service NotFound"],
        },
    }
    evidence_ids = list(evidence_store.keys())
    resolved_context = {
        "cluster_explicitly_provided": True, "cluster_name": "sre-test-cluster",
        "namespace": "test-incidents", "pod": "inventory-service", "mcp_source": "gke_remote_mcp",
    }
    rca_result = {
        "claims": [
            {
                "claim_type": "observed_fact",
                "text": "The Kubernetes Service named 'db-service' was not found in the cluster.",
                "supporting_evidence_ids": ["ev_003"],
            },
        ],
    }
    claims = build_claims(rca_result, evidence_ids, evidence_store)
    tool_history = [
        {"step": 1, "tool": "get_k8s_resource", "mcp_source": "gke_remote_mcp", "ok": True},
    ]
    return claims, evidence_store, tool_history, resolved_context


def test_configmap_evidence_no_longer_dinged_for_not_containing_pod_name():
    claims, evidence_store, tool_history, resolved_context = _configmap_001_fixture()
    result = score_root_cause_confidence(
        claims=claims, contradictions=[], hypotheses=[],
        evidence_store=evidence_store, tool_history=tool_history,
        resolved_context=resolved_context, incident_type="ContainerCreating", policy=POLICY,
    )
    assert result["components"]["resource_identity_match"] == 1.0
    assert not any("different namespace/pod" in r for r in result["reasons"])


def test_service_evidence_no_longer_dinged_for_not_containing_pod_name():
    claims, evidence_store, tool_history, resolved_context = _init_001_fixture()
    result = score_root_cause_confidence(
        claims=claims, contradictions=[], hypotheses=[],
        evidence_store=evidence_store, tool_history=tool_history,
        resolved_context=resolved_context, incident_type="Init", policy=POLICY,
    )
    assert result["components"]["resource_identity_match"] == 1.0
    assert not any("different namespace/pod" in r for r in result["reasons"])


def test_wrong_pod_evidence_is_still_correctly_flagged():
    """Guards against over-correction: evidence genuinely about a DIFFERENT pod (resource_type
    == "pod", explicitly) must still be flagged. Only the non-Pod resource_type case is relaxed."""
    evidence_store = {
        "ev_001": {
            "ok": True, "tool": "get_k8s_resource", "mcp_source": "gke_remote_mcp",
            "cluster": "sre-test-cluster", "resource_type": "pod",
            "resource_id": "test-incidents/some-other-pod",
            "summary": "Pod some-other-pod is Running",
            "key_facts": ["Pod some-other-pod status Running"],
        },
    }
    evidence_ids = list(evidence_store.keys())
    resolved_context = {
        "cluster_explicitly_provided": True, "cluster_name": "sre-test-cluster",
        "namespace": "test-incidents", "pod": "target-pod", "mcp_source": "gke_remote_mcp",
    }
    rca_result = {
        "claims": [
            {
                "claim_type": "observed_fact",
                "text": "Pod some-other-pod is Running.",
                "supporting_evidence_ids": ["ev_001"],
            },
        ],
    }
    claims = build_claims(rca_result, evidence_ids, evidence_store)
    tool_history = [{"step": 1, "tool": "get_k8s_resource", "mcp_source": "gke_remote_mcp", "ok": True}]
    result = score_root_cause_confidence(
        claims=claims, contradictions=[], hypotheses=[],
        evidence_store=evidence_store, tool_history=tool_history,
        resolved_context=resolved_context, incident_type="Unknown", policy=POLICY,
    )
    assert result["components"]["resource_identity_match"] < 1.0
    assert any("different namespace/pod" in r for r in result["reasons"])


def test_cross_namespace_non_pod_evidence_is_still_correctly_flagged():
    """Guards against over-correction: namespace containment still applies unconditionally,
    even for non-Pod resource types."""
    evidence_store = {
        "ev_001": {
            "ok": True, "tool": "get_k8s_resource", "mcp_source": "gke_remote_mcp",
            "cluster": "sre-test-cluster", "resource_type": "configmap",
            "resource_id": "wrong-namespace/some-config",
            "summary": "ConfigMap some-config in wrong-namespace",
            "key_facts": ["ConfigMap some-config exists"],
        },
    }
    evidence_ids = list(evidence_store.keys())
    resolved_context = {
        "cluster_explicitly_provided": True, "cluster_name": "sre-test-cluster",
        "namespace": "test-incidents", "pod": "auth-service", "mcp_source": "gke_remote_mcp",
    }
    rca_result = {
        "claims": [
            {
                "claim_type": "observed_fact",
                "text": "ConfigMap some-config exists in wrong-namespace.",
                "supporting_evidence_ids": ["ev_001"],
            },
        ],
    }
    claims = build_claims(rca_result, evidence_ids, evidence_store)
    tool_history = [{"step": 1, "tool": "get_k8s_resource", "mcp_source": "gke_remote_mcp", "ok": True}]
    result = score_root_cause_confidence(
        claims=claims, contradictions=[], hypotheses=[],
        evidence_store=evidence_store, tool_history=tool_history,
        resolved_context=resolved_context, incident_type="Unknown", policy=POLICY,
    )
    assert result["components"]["resource_identity_match"] < 1.0
    assert any("different namespace/pod" in r for r in result["reasons"])
