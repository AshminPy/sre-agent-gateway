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
        "expected_trajectory": ["list_pods", "get_pod_logs", "get_events"],
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
        "expected_trajectory": ["list_pods", "get_pod", "get_pod_logs"],
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
        "expected_trajectory": ["list_pods", "get_events"],
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
        "expected_trajectory": ["list_pods", "get_events", "describe_pod"],
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
        "expected_trajectory": ["list_pods", "get_events"],
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
        "expected_trajectory": ["list_pods", "get_events"],
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
        "expected_trajectory": ["list_pods", "get_pod_logs", "get_events"],
        "expected_keywords": ["database", "order-db", "OOMKilled"],
        "expected_confidence_min": 0.50,
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
        "expected_trajectory": ["list_pods", "get_events", "describe_pod"],
        "expected_keywords": ["Secret", "db-credentials", "ContainerCreating"],
        "expected_confidence_min": 0.65,
    },
]
