from agent.confidence.evidence_domains import EvidenceDomain, classify_tool, domain_weight


def test_classify_custom_k8s_tool():
    assert classify_tool("get_current_logs") == EvidenceDomain.CURRENT_LOGS
    assert classify_tool("get_previous_logs") == EvidenceDomain.PREVIOUS_LOGS
    assert classify_tool("list_events") == EvidenceDomain.KUBERNETES_EVENTS
    assert classify_tool("describe_pod_detail") == EvidenceDomain.KUBERNETES_STATUS
    assert classify_tool("list_deployments") == EvidenceDomain.WORKLOAD_CONFIG


def test_classify_gke_remote_tool_normalizes_to_same_domain_as_custom_equivalent():
    # get_k8s_logs (GKE) must land in the same domain as get_current_logs (custom) —
    # this is what "support future MCP sources without rewriting the core scorer" means.
    assert classify_tool("get_k8s_logs") == classify_tool("get_current_logs")
    assert classify_tool("list_k8s_events") == classify_tool("list_events")


def test_classify_unknown_tool_returns_unknown_not_a_guess():
    assert classify_tool("some_future_prometheus_tool") == EvidenceDomain.UNKNOWN
    assert classify_tool("") == EvidenceDomain.UNKNOWN
    assert classify_tool(None) == EvidenceDomain.UNKNOWN


def test_get_k8s_logs_previous_true_classifies_as_previous_logs():
    # Confirmed live bug, docs/management/confidence-genericity-review-2026-08-28.md #15.6:
    # a real production oomkilled-001 run called get_k8s_logs(previous=True) and it silently
    # classified as CURRENT_LOGS, causing a false "missing previous_logs" gap.
    assert classify_tool("get_k8s_logs", {"previous": True}) == EvidenceDomain.PREVIOUS_LOGS


def test_get_k8s_logs_previous_false_still_classifies_as_current_logs():
    # Regression: the common case (previous omitted/false) must not change.
    assert classify_tool("get_k8s_logs", {"previous": False}) == EvidenceDomain.CURRENT_LOGS
    assert classify_tool("get_k8s_logs", {}) == EvidenceDomain.CURRENT_LOGS
    assert classify_tool("get_k8s_logs") == EvidenceDomain.CURRENT_LOGS


def test_describe_k8s_resource_resource_type_selects_workload_config_domain():
    assert classify_tool("describe_k8s_resource", {"resourceType": "deployment"}) == EvidenceDomain.WORKLOAD_CONFIG
    assert classify_tool("get_k8s_resource", {"resourceType": "replicaset"}) == EvidenceDomain.WORKLOAD_CONFIG


def test_describe_k8s_resource_resource_type_pod_is_unchanged():
    assert classify_tool("describe_k8s_resource", {"resourceType": "pod"}) == EvidenceDomain.KUBERNETES_STATUS
    assert classify_tool("describe_k8s_resource") == EvidenceDomain.KUBERNETES_STATUS


def test_describe_k8s_resource_unmapped_resource_type_falls_back_unchanged():
    # service/node/configmap/job intentionally have no domain entry yet (report #2 row 1's
    # open item) -- must fall back to today's tool-name-only behavior, not guess or crash.
    assert classify_tool("describe_k8s_resource", {"resourceType": "service"}) == EvidenceDomain.KUBERNETES_STATUS


def test_classify_tool_ignores_args_for_tools_with_no_override():
    # A tool not in _ARGS_DOMAIN_OVERRIDES must classify identically regardless of args.
    assert classify_tool("list_events", {"namespace": "test-incidents"}) == EvidenceDomain.KUBERNETES_EVENTS


def test_related_domains_weighted_down_when_both_present():
    both = {EvidenceDomain.CURRENT_LOGS, EvidenceDomain.PREVIOUS_LOGS}
    assert domain_weight(EvidenceDomain.CURRENT_LOGS, both) == 0.5
    assert domain_weight(EvidenceDomain.PREVIOUS_LOGS, both) == 0.5


def test_unrelated_domain_full_weight():
    present = {EvidenceDomain.KUBERNETES_STATUS, EvidenceDomain.KUBERNETES_EVENTS}
    assert domain_weight(EvidenceDomain.KUBERNETES_STATUS, present) == 1.0


def test_single_log_domain_alone_full_weight():
    # current_logs present WITHOUT previous_logs is still a full independent source —
    # the discount only applies when BOTH related domains are actually present together.
    present = {EvidenceDomain.CURRENT_LOGS}
    assert domain_weight(EvidenceDomain.CURRENT_LOGS, present) == 1.0
