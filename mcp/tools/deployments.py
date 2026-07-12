from kubernetes import client


def get_deployments(apps_v1: client.AppsV1Api, namespace: str) -> dict:
    """List deployments with replica status."""
    deployments = apps_v1.list_namespaced_deployment(namespace=namespace)
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
