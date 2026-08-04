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
