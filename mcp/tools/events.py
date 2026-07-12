from kubernetes import client


def get_events(
    v1: client.CoreV1Api,
    namespace: str,
    pod_name: str | None = None,
) -> dict:
    """Get Kubernetes events — ImagePullBackOff reason shows here."""
    events = v1.list_namespaced_event(namespace=namespace)
    result = []
    for e in events.items:
        if pod_name and e.involved_object.name != pod_name:
            continue
        result.append({
            "name": e.metadata.name,
            "namespace": e.metadata.namespace,
            "reason": e.reason,
            "message": e.message,
            "type": e.type,
            "count": e.count,
            "first_time": str(e.first_timestamp),
            "last_time": str(e.last_timestamp),
            "involved_object": {
                "kind": e.involved_object.kind,
                "name": e.involved_object.name,
                "namespace": e.involved_object.namespace,
            },
        })
    # Sort by last_time descending — most recent first
    result.sort(key=lambda x: x["last_time"], reverse=True)
    return {
        "namespace": namespace,
        "pod_filter": pod_name,
        "events": result,
        "count": len(result),
    }
