"""HorizontalPodAutoscaler tools — scaling evidence for SRE investigation."""
from kubernetes import client

from security import REQUEST_TIMEOUT


def get_hpas(autoscaling_v2: client.AutoscalingV2Api, namespace: str) -> dict:
    """List HPAs with current/desired/min/max replicas."""
    items = autoscaling_v2.list_namespaced_horizontal_pod_autoscaler(
        namespace=namespace, _request_timeout=REQUEST_TIMEOUT,
    )
    result = []
    for h in items.items:
        result.append({
            "name": h.metadata.name,
            "namespace": h.metadata.namespace,
            "target": {
                "kind": h.spec.scale_target_ref.kind,
                "name": h.spec.scale_target_ref.name,
            },
            "min_replicas": h.spec.min_replicas,
            "max_replicas": h.spec.max_replicas,
            "current_replicas": h.status.current_replicas,
            "desired_replicas": h.status.desired_replicas,
        })
    return {"hpas": result, "count": len(result)}


def describe_hpa(autoscaling_v2: client.AutoscalingV2Api, namespace: str, name: str) -> dict:
    """Full HPA description including per-metric current/target values and conditions."""
    h = autoscaling_v2.read_namespaced_horizontal_pod_autoscaler(
        name=name, namespace=namespace, _request_timeout=REQUEST_TIMEOUT,
    )
    metrics = []
    for m in (h.spec.metrics or []):
        metrics.append({"type": m.type})
    return {
        "name": h.metadata.name,
        "namespace": h.metadata.namespace,
        "target": {"kind": h.spec.scale_target_ref.kind, "name": h.spec.scale_target_ref.name},
        "min_replicas": h.spec.min_replicas,
        "max_replicas": h.spec.max_replicas,
        "current_replicas": h.status.current_replicas,
        "desired_replicas": h.status.desired_replicas,
        "metrics_configured": metrics,
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (h.status.conditions or [])
        ],
    }
