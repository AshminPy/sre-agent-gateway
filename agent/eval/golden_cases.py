"""
Golden test cases for SRE Agent trajectory evaluation.

Each case maps to a scenario in eval/dataset.jsonl and adds:
- expected_trajectory: ordered list of tool names the agent SHOULD call
- expected_keywords: strings that MUST appear in likely_root_cause
- expected_confidence_min: minimum acceptable confidence score

These are used by run_eval.py for both local scoring and
Vertex AI Gen AI Evaluation Service trajectory metrics.

Reference: https://cloud.google.com/vertex-ai/generative-ai/docs/models/evaluation-agents
"""

GOLDEN_CASES = [
    {
        "id": "crashloop-001",
        "payload": {
            "user_query": "Pod crashloop-pod in test-incidents keeps crashing. Investigate and give root cause.",
            "incident": {"severity": "P2"},
            "resource_hints": {
                "namespace": "test-incidents",
                "pod": "crashloop-pod",
                "cluster": "sre-test-cluster",
            },
        },
        # Tools MUST be called in this order (at minimum). Extra calls are allowed.
        # Names match GKE_REMOTE_TOOLS (agent/mcp_client.py) — this case targets
        # sre-test-cluster (type=gke), so it routes through gke_remote_mcp, never
        # the custom MCP's differently-named CUSTOM_K8S_TOOLS. Reasoned from the
        # incident type (crash reason lives in logs; loop pattern confirmed by
        # events), not copied from any one observed run.
        "expected_trajectory": ["get_k8s_logs", "list_k8s_events"],
        "expected_keywords": ["CrashLoopBackOff", "exit"],
        "expected_confidence_min": 0.65,
    },
    {
        "id": "oomkilled-001",
        "payload": {
            "user_query": "Pod oomkilled-pod in test-incidents is OOMKilled repeatedly. What is happening?",
            "incident": {"severity": "P1"},
            "resource_hints": {
                "namespace": "test-incidents",
                "pod": "oomkilled-pod",
                "cluster": "sre-test-cluster",
            },
        },
        # GKE_REMOTE_TOOLS names. Resource status carries the OOMKilled reason
        # (exit 137, memory limit); logs corroborate.
        "expected_trajectory": ["get_k8s_resource", "get_k8s_logs"],
        "expected_keywords": ["OOMKilled", "137", "memory"],
        "expected_confidence_min": 0.65,
    },
    {
        "id": "imagepull-001",
        "payload": {
            "user_query": "Pod imagepull-pod in test-incidents cannot pull its image. Investigate.",
            "incident": {"severity": "P2"},
            "resource_hints": {
                "namespace": "test-incidents",
                "pod": "imagepull-pod",
                "cluster": "sre-test-cluster",
            },
        },
        # GKE_REMOTE_TOOLS names. Resource status carries the pull-error message;
        # events confirm the ImagePullBackOff/ErrImagePull pattern.
        "expected_trajectory": ["get_k8s_resource", "list_k8s_events"],
        "expected_keywords": ["ImagePullBackOff", "ErrImagePull", "image"],
        "expected_confidence_min": 0.65,
    },
    {
        "id": "configmap-001",
        "payload": {
            "user_query": "Pod auth-service in test-incidents is stuck ContainerCreating for 8 minutes. Users cannot authenticate. Investigate the startup failure.",
            "incident": {"severity": "P1"},
            "resource_hints": {
                "namespace": "test-incidents",
                "pod": "auth-service",
                "cluster": "sre-test-cluster",
            },
        },
        # GKE_REMOTE_TOOLS names. Events show why it's stuck; describe surfaces
        # the mount/ConfigMap detail.
        "expected_trajectory": ["list_k8s_events", "describe_k8s_resource"],
        "expected_keywords": ["ContainerCreating", "ConfigMap", "app-config"],
        "expected_confidence_min": 0.65,
    },
    {
        "id": "init-001",
        "payload": {
            "user_query": "Pod inventory-service in test-incidents has been Init:0/2 for 15 minutes. The app never started. What is blocking it?",
            "incident": {"severity": "P2"},
            "resource_hints": {
                "namespace": "test-incidents",
                "pod": "inventory-service",
                "cluster": "sre-test-cluster",
            },
        },
        # GKE_REMOTE_TOOLS names. Events show what's blocking the init phase;
        # resource status shows the init container's own state.
        "expected_trajectory": ["list_k8s_events", "get_k8s_resource"],
        "expected_keywords": ["Init", "init container", "db-service"],
        "expected_confidence_min": 0.65,
    },
    {
        "id": "selector-001",
        "payload": {
            "user_query": "notification-svc in test-incidents shows no endpoints. Pods are Running. Users report 503 errors. Investigate why traffic is not reaching the pods.",
            "incident": {"severity": "P2"},
            "resource_hints": {
                "namespace": "test-incidents",
                "cluster": "sre-test-cluster",
            },
        },
        # GKE_REMOTE_TOOLS names. This is a label/selector mismatch, not a crash —
        # events on the pods won't show it. Describe surfaces the pod's labels;
        # get_k8s_resource on the Service surfaces its selector, so the mismatch
        # is visible by comparing the two.
        "expected_trajectory": ["describe_k8s_resource", "get_k8s_resource"],
        "expected_keywords": ["selector", "endpoint", "label"],
        "expected_confidence_min": 0.50,
    },
    {
        "id": "cascading-001",
        "payload": {
            "user_query": "order-api in test-incidents is CrashLoopBackOff. Payment orders are failing. Identify the true root cause.",
            "incident": {"severity": "P1"},
            "resource_hints": {
                "namespace": "test-incidents",
                "cluster": "sre-test-cluster",
            },
        },
        # GKE_REMOTE_TOOLS names. Logs on order-api should reveal the true
        # upstream cause (order-db); events corroborate the crash pattern.
        # This is the MINIMUM for the first pod investigated — a real
        # cascading-failure investigation may call more tools tracing into
        # order-db, which is allowed (extra calls are fine, per the rule above).
        "expected_trajectory": ["get_k8s_logs", "list_k8s_events"],
        "expected_keywords": ["database", "order-db", "OOMKilled"],
        "expected_confidence_min": 0.50,
    },
    {
        "id": "pending-001",
        "payload": {
            "user_query": "Pod batch-worker-7 in test-incidents has been Pending for 12 minutes and never scheduled. Investigate why it will not start.",
            "incident": {"severity": "P2"},
            "resource_hints": {
                "namespace": "test-incidents",
                "pod": "batch-worker-7",
                "cluster": "sre-test-cluster",
            },
        },
        # GKE_REMOTE_TOOLS names. Events carry the FailedScheduling reason;
        # resource status shows the pod's own resource requests for context.
        "expected_trajectory": ["list_k8s_events", "get_k8s_resource"],
        "expected_keywords": ["Pending", "FailedScheduling", "Insufficient"],
        "expected_confidence_min": 0.50,
    },
    {
        # Non-GKE cluster — must route to the custom K8s MCP (k8s_mcp), never gke_remote_mcp.
        # Requires clusters.json in the live cluster registry to declare this cluster with
        # "type": "custom" — see agent/mcp_client.py:_build_cluster_registry(). Deterministic
        # routing behavior for this case (correct MCP source selected for a non-GKE cluster) is
        # additionally covered without live infra by
        # tests/test_eval_scenario_matrix.py::test_non_gke_cluster_routes_to_custom_mcp.
        "id": "onprem-001",
        "payload": {
            "user_query": "Pod legacy-billing-0 in billing-ns on our on-prem cluster is CrashLoopBackOff. Investigate.",
            "incident": {"severity": "P2"},
            "resource_hints": {
                "namespace": "billing-ns",
                "pod": "legacy-billing-0",
                "cluster": "onprem-dc1-cluster",
            },
        },
        # NOT changed in the 2026-08-09 stale-name fix — these three names
        # (list_pods, get_current_logs, list_events) are CUSTOM_K8S_TOOLS
        # names (agent/mcp_client.py), which is correct here since this case
        # routes through k8s_mcp, not gke_remote_mcp. They were never stale;
        # they just happen to look similar to the old GKE-side names that
        # WERE stale in every other case in this file.
        "expected_trajectory": ["list_pods", "get_current_logs", "list_events"],
        "expected_keywords": ["CrashLoopBackOff", "exit"],
        "expected_confidence_min": 0.50,
    },
    {
        # Deliberately thin evidence available (pod already deleted / logs rotated out) — the
        # agent must land on insufficient_evidence / a low confidence score, not invent a root
        # cause. Full grounding/claim-exclusion behavior is validated without live infra by
        # tests/test_eval_scenario_matrix.py::test_insufficient_evidence_does_not_invent_a_cause.
        "id": "insufficient-evidence-001",
        "payload": {
            "user_query": "Pod ghost-pod-42 in test-incidents was reported crashing but no longer exists. Investigate what happened.",
            "incident": {"severity": "P3"},
            "resource_hints": {
                "namespace": "test-incidents",
                "pod": "ghost-pod-42",
                "cluster": "sre-test-cluster",
            },
        },
        # GKE_REMOTE_TOOLS name. A single resource lookup is enough to discover
        # the pod is gone — that absence itself is the evidence for this case.
        "expected_trajectory": ["get_k8s_resource"],
        "expected_keywords": ["unknown", "insufficient"],
        "expected_confidence_min": 0.0,
        "expected_outcome": ["insufficient_evidence", "unknown"],
        "max_confidence": 0.5,
    },
    {
        # Evidence collected references a different cluster than the one the investigation is
        # scoped to (stale cache / cross-cluster name collision) — must surface as
        # conflicting_evidence, not be silently averaged away. Validated without live infra by
        # tests/test_eval_scenario_matrix.py::test_conflicting_evidence_forces_conflicting_outcome.
        "id": "conflicting-evidence-001",
        "payload": {
            "user_query": "Pod shared-cache-2 in test-incidents is unstable. Investigate — evidence sources disagree on cluster origin.",
            "incident": {"severity": "P2"},
            "resource_hints": {
                "namespace": "test-incidents",
                "pod": "shared-cache-2",
                "cluster": "sre-test-cluster",
            },
        },
        # GKE_REMOTE_TOOLS names. Resource lookup plus events surfaces the
        # cross-cluster disagreement in the collected evidence.
        "expected_trajectory": ["get_k8s_resource", "list_k8s_events"],
        "expected_keywords": ["conflicting", "cluster"],
        "expected_confidence_min": 0.0,
        "expected_outcome": ["conflicting_evidence"],
        "max_confidence": 0.65,
    },
    {
        # No cluster_hint and no unique project/environment/namespace match — must trigger
        # Task 1's human safe-stop (resolve_cluster_routing tier 5), never guess a cluster.
        # Validated without live infra by tests/test_eval_scenario_matrix.py::
        # test_ambiguous_routing_triggers_safe_stop_and_insufficient_evidence.
        "id": "ambiguous-routing-001",
        "payload": {
            "user_query": "Something is wrong with the checkout pod. Investigate.",
            "incident": {"severity": "P2"},
            "resource_hints": {
                "namespace": "checkout",
            },
        },
        "expected_trajectory": [],
        "expected_keywords": ["unknown", "cluster"],
        "expected_confidence_min": 0.0,
        "expected_outcome": ["insufficient_evidence"],
        "max_confidence": 0.0,
    },
    {
        # GKE Remote MCP (or Connect Gateway) call fails outright — must surface as a tool
        # failure, never a silent empty success, and must degrade completeness/confidence
        # rather than being ignored. Validated without live infra by tests/
        # test_eval_scenario_matrix.py::test_gke_remote_mcp_network_failure_surfaces_as_tool_failure.
        "id": "mcp-gateway-failure-001",
        "payload": {
            "user_query": "Pod edge-gateway-1 in test-incidents is failing. Investigate — GKE Remote MCP / Connect Gateway is degraded.",
            "incident": {"severity": "P1"},
            "resource_hints": {
                "namespace": "test-incidents",
                "pod": "edge-gateway-1",
                "cluster": "sre-test-cluster",
            },
        },
        # GKE_REMOTE_TOOLS name. The attempted resource lookup is what fails
        # (GKE Remote MCP degraded) — that failure itself is the case under test.
        "expected_trajectory": ["get_k8s_resource"],
        "expected_keywords": ["unknown", "failed"],
        "expected_confidence_min": 0.0,
        "expected_outcome": ["insufficient_evidence", "unknown"],
        "max_confidence": 0.5,
    },
    {
        "id": "secret-001",
        "payload": {
            "user_query": "user-service in demo-incidents has been ContainerCreating for 10 minutes. All user authentication is broken. Investigate the startup failure.",
            "incident": {"severity": "P1"},
            "resource_hints": {
                "namespace": "demo-incidents",
                "pod": "user-service",
                "cluster": "sre-test-cluster",
            },
        },
        # GKE_REMOTE_TOOLS names. Events carry the missing-Secret mount reason;
        # describe_k8s_resource confirms the volume/secret reference on the pod spec.
        "expected_trajectory": ["list_k8s_events", "describe_k8s_resource"],
        "expected_keywords": ["Secret", "db-credentials", "ContainerCreating"],
        "expected_confidence_min": 0.65,
    },
]
