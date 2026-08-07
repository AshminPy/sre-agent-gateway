"""
Unit tests for the P4 "missing ops" tool modules — StatefulSet/DaemonSet,
Service/endpoints, resource requests/limits, config metadata, rollout info.

Uses lightweight SimpleNamespace fakes shaped like kubernetes-client response
objects (same attribute-access pattern: obj.metadata.name, obj.status.x, ...)
rather than mocking the whole kubernetes SDK — keeps tests fast and focused
on OUR mapping logic, not the SDK's.
"""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

from tools.deployments import describe_deployment, get_replicasets, describe_replicaset
from tools.workloads import get_statefulsets, describe_statefulset, get_daemonsets, describe_daemonset
from tools.services import get_services, describe_service, get_endpoints
from tools.nodes import get_nodes, describe_node
from tools.configmaps import get_configmaps, get_configmap
from tools.scaling import get_hpas, describe_hpa
from tools.storage import get_pvcs, describe_pvc
from tools.jobs import get_jobs, describe_job
from tools.events import get_namespace_events


def _container(name="app", image="nginx:1.25", requests=None, limits=None):
    return NS(
        name=name,
        image=image,
        resources=NS(requests=requests or {"cpu": "100m"}, limits=limits or {"memory": "256Mi"}),
    )


# ── Deployment (describe) / ReplicaSet ───────────────────────────────

def test_describe_deployment_maps_resources_and_rollout_info():
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_deployment.return_value = NS(
        metadata=NS(name="api", namespace="prod", generation=3, labels={"app": "api"}, annotations={}),
        status=NS(
            observed_generation=3, ready_replicas=2, available_replicas=2,
            updated_replicas=2, unavailable_replicas=None,
            conditions=[NS(type="Available", status="True", reason="MinimumReplicasAvailable",
                            message="ok", last_update_time="2026-01-01")],
        ),
        spec=NS(replicas=2, strategy=NS(type="RollingUpdate"),
                template=NS(spec=NS(containers=[_container()]))),
    )
    result = describe_deployment(apps_v1, "prod", "api")
    apps_v1.read_namespaced_deployment.assert_called_once()
    assert result["name"] == "api"
    assert result["replicas"]["desired"] == 2
    assert result["strategy"]["type"] == "RollingUpdate"
    assert result["containers"][0]["resources"]["requests"] == {"cpu": "100m"}
    assert result["containers"][0]["resources"]["limits"] == {"memory": "256Mi"}


def test_get_replicasets_includes_owner_reference():
    apps_v1 = MagicMock()
    apps_v1.list_namespaced_replica_set.return_value = NS(items=[
        NS(metadata=NS(name="api-abc123", namespace="prod", labels={},
                       owner_references=[NS(kind="Deployment", name="api")]),
           spec=NS(replicas=2),
           status=NS(ready_replicas=2, available_replicas=2)),
    ])
    result = get_replicasets(apps_v1, "prod")
    assert result["count"] == 1
    assert result["replicasets"][0]["owner"] == [{"kind": "Deployment", "name": "api"}]


def test_describe_replicaset_full_detail():
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_replica_set.return_value = NS(
        metadata=NS(name="api-abc123", namespace="prod", labels={},
                    owner_references=[NS(kind="Deployment", name="api")]),
        spec=NS(replicas=2, template=NS(spec=NS(containers=[_container()]))),
        status=NS(ready_replicas=2, available_replicas=2, fully_labeled_replicas=2, conditions=[]),
    )
    result = describe_replicaset(apps_v1, "prod", "api-abc123")
    assert result["replicas"]["fully_labeled"] == 2


# ── StatefulSet / DaemonSet ───────────────────────────────────────────

def test_get_statefulsets_list():
    apps_v1 = MagicMock()
    apps_v1.list_namespaced_stateful_set.return_value = NS(items=[
        NS(metadata=NS(name="db", namespace="prod", labels={}),
           spec=NS(replicas=3, service_name="db-headless"),
           status=NS(ready_replicas=3, current_replicas=3, updated_replicas=3)),
    ])
    result = get_statefulsets(apps_v1, "prod")
    assert result["count"] == 1
    assert result["statefulsets"][0]["service_name"] == "db-headless"


def test_describe_statefulset_includes_container_resources():
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_stateful_set.return_value = NS(
        metadata=NS(name="db", namespace="prod", generation=1, labels={}, annotations={}),
        spec=NS(replicas=3, service_name="db-headless", update_strategy=NS(type="RollingUpdate"),
                template=NS(spec=NS(containers=[_container(name="postgres")]))),
        status=NS(observed_generation=1, ready_replicas=3, current_replicas=3, updated_replicas=3),
    )
    result = describe_statefulset(apps_v1, "prod", "db")
    assert result["containers"][0]["name"] == "postgres"
    assert result["update_strategy"] == "RollingUpdate"


def test_get_daemonsets_status_fields():
    apps_v1 = MagicMock()
    apps_v1.list_namespaced_daemon_set.return_value = NS(items=[
        NS(metadata=NS(name="fluentd", namespace="kube-system", labels={}),
           status=NS(desired_number_scheduled=3, current_number_scheduled=3, number_ready=3,
                     number_available=3, number_unavailable=0, number_misscheduled=0,
                     updated_number_scheduled=3)),
    ])
    result = get_daemonsets(apps_v1, "kube-system")
    assert result["daemonsets"][0]["status"]["number_ready"] == 3


def test_describe_daemonset_full_detail():
    apps_v1 = MagicMock()
    apps_v1.read_namespaced_daemon_set.return_value = NS(
        metadata=NS(name="fluentd", namespace="kube-system", generation=1, labels={}, annotations={}),
        spec=NS(update_strategy=NS(type="RollingUpdate"), template=NS(spec=NS(containers=[_container()]))),
        status=NS(observed_generation=1, desired_number_scheduled=3, current_number_scheduled=3,
                  number_ready=3, number_available=3, number_unavailable=0,
                  updated_number_scheduled=3, conditions=[]),
    )
    result = describe_daemonset(apps_v1, "kube-system", "fluentd")
    assert result["status"]["desired_number_scheduled"] == 3


# ── Service / Endpoints ────────────────────────────────────────────────

def test_get_services_list():
    v1 = MagicMock()
    v1.list_namespaced_service.return_value = NS(items=[
        NS(metadata=NS(name="api", namespace="prod", labels={}),
           spec=NS(type="ClusterIP", cluster_ip="10.0.0.5", selector={"app": "api"},
                   ports=[NS(name="http", port=80, target_port=8080, protocol="TCP")])),
    ])
    result = get_services(v1, "prod")
    assert result["services"][0]["type"] == "ClusterIP"
    assert result["services"][0]["ports"][0]["port"] == 80


def test_describe_service_full_detail():
    v1 = MagicMock()
    v1.read_namespaced_service.return_value = NS(
        metadata=NS(name="api", namespace="prod", labels={}, annotations={}),
        spec=NS(type="ClusterIP", cluster_ip="10.0.0.5", external_ips=[],
                session_affinity="None", selector={"app": "api"},
                ports=[NS(name="http", port=80, target_port=8080, protocol="TCP", node_port=None)]),
    )
    result = describe_service(v1, "prod", "api")
    assert result["cluster_ip"] == "10.0.0.5"
    assert result["ports"][0]["node_port"] is None


def test_get_endpoints_reports_ready_addresses():
    v1 = MagicMock()
    v1.list_namespaced_endpoints.return_value = NS(items=[
        NS(metadata=NS(name="api", namespace="prod"),
           subsets=[NS(addresses=[NS(ip="10.0.0.10")], not_ready_addresses=[],
                       ports=[NS(name="http", port=8080, protocol="TCP")])]),
    ])
    result = get_endpoints(v1, "prod")
    assert result["endpoints"][0]["has_ready_addresses"] is True


def test_get_endpoints_reports_no_ready_addresses_when_empty():
    """Empty endpoints — the classic 'Service has no healthy backends' evidence."""
    v1 = MagicMock()
    v1.list_namespaced_endpoints.return_value = NS(items=[
        NS(metadata=NS(name="api", namespace="prod"),
           subsets=[NS(addresses=[], not_ready_addresses=[NS(ip="10.0.0.10")],
                       ports=[NS(name="http", port=8080, protocol="TCP")])]),
    ])
    result = get_endpoints(v1, "prod")
    assert result["endpoints"][0]["has_ready_addresses"] is False
    assert result["endpoints"][0]["subsets"][0]["not_ready_addresses"] == ["10.0.0.10"]


# ── Node (cluster-scoped) ──────────────────────────────────────────────

def test_get_nodes_ready_flag():
    v1 = MagicMock()
    v1.list_node.return_value = NS(items=[
        NS(metadata=NS(name="node-1", labels={}),
           status=NS(conditions=[NS(type="Ready", status="True")],
                     capacity={"cpu": "8"}, allocatable={"cpu": "7800m"},
                     node_info=NS(kubelet_version="v1.29.0"))),
    ])
    result = get_nodes(v1)
    assert result["nodes"][0]["ready"] is True


def test_describe_node_includes_taints():
    v1 = MagicMock()
    v1.read_node.return_value = NS(
        metadata=NS(name="node-1", labels={}, annotations={}),
        status=NS(conditions=[], capacity={}, allocatable={}, addresses=[],
                  node_info=NS(kubelet_version="v1.29.0", os_image="Ubuntu")),
        spec=NS(taints=[NS(key="dedicated", value="gpu", effect="NoSchedule")], unschedulable=False),
    )
    result = describe_node(v1, "node-1")
    assert result["taints"] == [{"key": "dedicated", "value": "gpu", "effect": "NoSchedule"}]


# ── ConfigMap ──────────────────────────────────────────────────────────

def test_get_configmaps_lists_keys_not_values():
    v1 = MagicMock()
    v1.list_namespaced_config_map.return_value = NS(items=[
        NS(metadata=NS(name="app-config", namespace="prod", labels={}),
           data={"LOG_LEVEL": "debug", "FEATURE_X": "on"}),
    ])
    result = get_configmaps(v1, "prod")
    assert result["configmaps"][0]["keys"] == ["FEATURE_X", "LOG_LEVEL"]
    assert "values" not in str(result)  # only key NAMES exposed at list level


def test_get_configmap_returns_data():
    v1 = MagicMock()
    v1.read_namespaced_config_map.return_value = NS(
        metadata=NS(name="app-config", namespace="prod", labels={}, annotations={}),
        data={"LOG_LEVEL": "debug"},
        binary_data={},
    )
    result = get_configmap(v1, "prod", "app-config")
    assert result["data"] == {"LOG_LEVEL": "debug"}


# ── HPA ──────────────────────────────────────────────────────────────

def test_get_hpas_replica_counts():
    a2 = MagicMock()
    a2.list_namespaced_horizontal_pod_autoscaler.return_value = NS(items=[
        NS(metadata=NS(name="api-hpa", namespace="prod"),
           spec=NS(scale_target_ref=NS(kind="Deployment", name="api"), min_replicas=2, max_replicas=10),
           status=NS(current_replicas=3, desired_replicas=3)),
    ])
    result = get_hpas(a2, "prod")
    assert result["hpas"][0]["max_replicas"] == 10


def test_describe_hpa_includes_conditions():
    a2 = MagicMock()
    a2.read_namespaced_horizontal_pod_autoscaler.return_value = NS(
        metadata=NS(name="api-hpa", namespace="prod"),
        spec=NS(scale_target_ref=NS(kind="Deployment", name="api"), min_replicas=2, max_replicas=10,
                metrics=[NS(type="Resource")]),
        status=NS(current_replicas=3, desired_replicas=3,
                  conditions=[NS(type="AbleToScale", status="True", reason="ReadyForNewScale", message="ok")]),
    )
    result = describe_hpa(a2, "prod", "api-hpa")
    assert result["conditions"][0]["type"] == "AbleToScale"


# ── PVC ────────────────────────────────────────────────────────────────

def test_get_pvcs_bound_status():
    v1 = MagicMock()
    v1.list_namespaced_persistent_volume_claim.return_value = NS(items=[
        NS(metadata=NS(name="data", namespace="prod"),
           status=NS(phase="Bound", capacity={"storage": "10Gi"}),
           spec=NS(storage_class_name="standard", access_modes=["ReadWriteOnce"], volume_name="pv-1")),
    ])
    result = get_pvcs(v1, "prod")
    assert result["pvcs"][0]["phase"] == "Bound"


def test_describe_pvc_pending_with_conditions():
    """The Pending/stuck-binding case this tool exists for."""
    v1 = MagicMock()
    v1.read_namespaced_persistent_volume_claim.return_value = NS(
        metadata=NS(name="data", namespace="prod", labels={}),
        status=NS(phase="Pending", capacity={},
                  conditions=[NS(type="Resizing", status="False", reason=None, message=None)]),
        spec=NS(storage_class_name="standard", access_modes=["ReadWriteOnce"], volume_name=None,
                resources=NS(requests={"storage": "10Gi"})),
    )
    result = describe_pvc(v1, "prod", "data")
    assert result["phase"] == "Pending"
    assert result["volume_name"] is None


# ── Jobs ──────────────────────────────────────────────────────────────

def test_get_jobs_completion_counts():
    batch_v1 = MagicMock()
    batch_v1.list_namespaced_job.return_value = NS(items=[
        NS(metadata=NS(name="migrate", namespace="prod"),
           status=NS(active=0, succeeded=1, failed=0, start_time="t1", completion_time="t2"),
           spec=NS(completions=1, parallelism=1)),
    ])
    result = get_jobs(batch_v1, "prod")
    assert result["jobs"][0]["succeeded"] == 1


def test_describe_job_failure_reason():
    batch_v1 = MagicMock()
    batch_v1.read_namespaced_job.return_value = NS(
        metadata=NS(name="migrate", namespace="prod", labels={}),
        status=NS(active=0, succeeded=0, failed=1, start_time="t1", completion_time=None,
                  conditions=[NS(type="Failed", status="True", reason="BackoffLimitExceeded",
                                 message="Job has reached the specified backoff limit")]),
        spec=NS(completions=1, parallelism=1, backoff_limit=3),
    )
    result = describe_job(batch_v1, "prod", "migrate")
    assert result["conditions"][0]["reason"] == "BackoffLimitExceeded"


# ── Namespace events (broader view) ────────────────────────────────────

def test_get_namespace_events_no_pod_filter():
    v1 = MagicMock()
    v1.list_namespaced_event.return_value = NS(items=[
        NS(metadata=NS(name="e1", namespace="prod"), reason="Scheduled", message="ok", type="Normal",
           count=1, first_timestamp="t1", last_timestamp="t2",
           involved_object=NS(kind="Pod", name="pod-a", namespace="prod")),
        NS(metadata=NS(name="e2", namespace="prod"), reason="FailedScheduling", message="no nodes",
           type="Warning", count=1, first_timestamp="t1", last_timestamp="t3",
           involved_object=NS(kind="Pod", name="pod-b", namespace="prod")),
    ])
    result = get_namespace_events(v1, "prod")
    assert result["pod_filter"] is None
    assert result["count"] == 2
