"""StatefulSet / DaemonSet tools — PRODUCTION-LAUNCH-PLAN.md P4 "missing ops"."""
from kubernetes import client

from security import REQUEST_TIMEOUT
from tools.deployments import _container_resources


def get_statefulsets(apps_v1: client.AppsV1Api, namespace: str) -> dict:
    """List StatefulSets with replica status."""
    items = apps_v1.list_namespaced_stateful_set(namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    result = []
    for s in items.items:
        result.append({
            "name": s.metadata.name,
            "namespace": s.metadata.namespace,
            "replicas": {
                "desired": s.spec.replicas,
                "ready": s.status.ready_replicas,
                "current": s.status.current_replicas,
                "updated": s.status.updated_replicas,
            },
            "service_name": s.spec.service_name,
            "labels": s.metadata.labels or {},
        })
    return {"statefulsets": result, "count": len(result)}


def describe_statefulset(apps_v1: client.AppsV1Api, namespace: str, name: str) -> dict:
    """Full StatefulSet description: resource requests/limits, rollout/update strategy."""
    s = apps_v1.read_namespaced_stateful_set(name=name, namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    return {
        "name": s.metadata.name,
        "namespace": s.metadata.namespace,
        "generation": s.metadata.generation,
        "observed_generation": s.status.observed_generation,
        "replicas": {
            "desired": s.spec.replicas,
            "ready": s.status.ready_replicas,
            "current": s.status.current_replicas,
            "updated": s.status.updated_replicas,
        },
        "service_name": s.spec.service_name,
        "update_strategy": s.spec.update_strategy.type if s.spec.update_strategy else None,
        "containers": _container_resources(s.spec.template.spec.containers if s.spec.template else []),
        "labels": s.metadata.labels or {},
        "annotations": s.metadata.annotations or {},
    }


def get_daemonsets(apps_v1: client.AppsV1Api, namespace: str) -> dict:
    """List DaemonSets with rollout status."""
    items = apps_v1.list_namespaced_daemon_set(namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    result = []
    for d in items.items:
        result.append({
            "name": d.metadata.name,
            "namespace": d.metadata.namespace,
            "status": {
                "desired_number_scheduled": d.status.desired_number_scheduled,
                "current_number_scheduled": d.status.current_number_scheduled,
                "number_ready": d.status.number_ready,
                "number_available": d.status.number_available,
                "number_unavailable": d.status.number_unavailable,
                "number_misscheduled": d.status.number_misscheduled,
                "updated_number_scheduled": d.status.updated_number_scheduled,
            },
            "labels": d.metadata.labels or {},
        })
    return {"daemonsets": result, "count": len(result)}


def describe_daemonset(apps_v1: client.AppsV1Api, namespace: str, name: str) -> dict:
    """Full DaemonSet description: resource requests/limits, rollout status."""
    d = apps_v1.read_namespaced_daemon_set(name=name, namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    return {
        "name": d.metadata.name,
        "namespace": d.metadata.namespace,
        "generation": d.metadata.generation,
        "observed_generation": d.status.observed_generation,
        "status": {
            "desired_number_scheduled": d.status.desired_number_scheduled,
            "current_number_scheduled": d.status.current_number_scheduled,
            "number_ready": d.status.number_ready,
            "number_available": d.status.number_available,
            "number_unavailable": d.status.number_unavailable,
            "updated_number_scheduled": d.status.updated_number_scheduled,
        },
        "update_strategy": d.spec.update_strategy.type if d.spec.update_strategy else None,
        "containers": _container_resources(d.spec.template.spec.containers if d.spec.template else []),
        "conditions": [
            {"type": c.type, "status": c.status, "message": c.message}
            for c in (d.status.conditions or [])
        ],
        "labels": d.metadata.labels or {},
        "annotations": d.metadata.annotations or {},
    }
