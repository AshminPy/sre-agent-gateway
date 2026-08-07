from kubernetes import client

from security import REQUEST_TIMEOUT


def get_events(
    v1: client.CoreV1Api,
    namespace: str,
    pod_name: str | None = None,
) -> dict:
    """Get Kubernetes events — ImagePullBackOff reason shows here."""
    events = v1.list_namespaced_event(namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
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


def get_namespace_events(v1: client.CoreV1Api, namespace: str) -> dict:
    """
    All events in the namespace, no pod filter — broader view than get_events
    for scanning a namespace-wide incident (e.g. an ImagePullBackOff hitting
    several pods, a quota rejection, a mass eviction).
    """
    return get_events(v1, namespace, pod_name=None)
