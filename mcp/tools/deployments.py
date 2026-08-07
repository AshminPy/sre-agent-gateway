from kubernetes import client

from security import REQUEST_TIMEOUT


def _container_resources(containers) -> list:
    """Resource requests/limits per container — used by describe_* tools."""
    out = []
    for c in containers or []:
        requests, limits = {}, {}
        if c.resources:
            requests = dict(c.resources.requests or {})
            limits = dict(c.resources.limits or {})
        out.append({
            "name": c.name,
            "image": c.image,
            "resources": {"requests": requests, "limits": limits},
        })
    return out


def get_deployments(apps_v1: client.AppsV1Api, namespace: str) -> dict:
    """List deployments with replica status."""
    deployments = apps_v1.list_namespaced_deployment(namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    result = []
    for d in deployments.items:
        result.append({
            "name": d.metadata.name,
            "namespace": d.metadata.namespace,
            "replicas": {
                "desired": d.spec.replicas,
                "ready": d.status.ready_replicas,
                "available": d.status.available_replicas,
                "updated": d.status.updated_replicas,
            },
            "conditions": [
                {
                    "type": c.type,
                    "status": c.status,
                    "reason": c.reason,
                    "message": c.message,
                }
                for c in (d.status.conditions or [])
            ],
            "labels": d.metadata.labels or {},
        })
    return {"deployments": result, "count": len(result)}


def describe_deployment(apps_v1: client.AppsV1Api, namespace: str, deployment_name: str) -> dict:
    """Full deployment description: strategy, resource requests/limits, rollout/change info."""
    d = apps_v1.read_namespaced_deployment(
        name=deployment_name, namespace=namespace, _request_timeout=REQUEST_TIMEOUT,
    )
    return {
        "name": d.metadata.name,
        "namespace": d.metadata.namespace,
        "generation": d.metadata.generation,
        "observed_generation": d.status.observed_generation,
        "replicas": {
            "desired": d.spec.replicas,
            "ready": d.status.ready_replicas,
            "available": d.status.available_replicas,
            "updated": d.status.updated_replicas,
            "unavailable": d.status.unavailable_replicas,
        },
        "strategy": {
            "type": d.spec.strategy.type if d.spec.strategy else None,
        },
        "containers": _container_resources(d.spec.template.spec.containers if d.spec.template else []),
        "conditions": [
            {
                "type": c.type,
                "status": c.status,
                "reason": c.reason,
                "message": c.message,
                "last_update_time": str(c.last_update_time),
            }
            for c in (d.status.conditions or [])
        ],
        "labels": d.metadata.labels or {},
        "annotations": d.metadata.annotations or {},
    }


def get_replicasets(apps_v1: client.AppsV1Api, namespace: str) -> dict:
    """List ReplicaSets with replica status — rollout/change history evidence."""
    rs_list = apps_v1.list_namespaced_replica_set(namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    result = []
    for rs in rs_list.items:
        result.append({
            "name": rs.metadata.name,
            "namespace": rs.metadata.namespace,
            "owner": [
                {"kind": o.kind, "name": o.name} for o in (rs.metadata.owner_references or [])
            ],
            "replicas": {
                "desired": rs.spec.replicas,
                "ready": rs.status.ready_replicas,
                "available": rs.status.available_replicas,
            },
            "labels": rs.metadata.labels or {},
        })
    return {"replicasets": result, "count": len(result)}


def describe_replicaset(apps_v1: client.AppsV1Api, namespace: str, replicaset_name: str) -> dict:
    """Full ReplicaSet description."""
    rs = apps_v1.read_namespaced_replica_set(
        name=replicaset_name, namespace=namespace, _request_timeout=REQUEST_TIMEOUT,
    )
    return {
        "name": rs.metadata.name,
        "namespace": rs.metadata.namespace,
        "owner": [{"kind": o.kind, "name": o.name} for o in (rs.metadata.owner_references or [])],
        "replicas": {
            "desired": rs.spec.replicas,
            "ready": rs.status.ready_replicas,
            "available": rs.status.available_replicas,
            "fully_labeled": rs.status.fully_labeled_replicas,
        },
        "containers": _container_resources(rs.spec.template.spec.containers if rs.spec.template else []),
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (rs.status.conditions or [])
        ],
        "labels": rs.metadata.labels or {},
    }
