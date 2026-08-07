"""PersistentVolumeClaim tools — storage evidence for SRE investigation."""
from kubernetes import client

from security import REQUEST_TIMEOUT


def get_pvcs(v1: client.CoreV1Api, namespace: str) -> dict:
    """List PVCs with bound status and capacity."""
    items = v1.list_namespaced_persistent_volume_claim(namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    result = []
    for p in items.items:
        result.append({
            "name": p.metadata.name,
            "namespace": p.metadata.namespace,
            "phase": p.status.phase,
            "capacity": dict(p.status.capacity or {}),
            "storage_class": p.spec.storage_class_name,
            "access_modes": p.spec.access_modes or [],
            "volume_name": p.spec.volume_name,
        })
    return {"pvcs": result, "count": len(result)}


def describe_pvc(v1: client.CoreV1Api, namespace: str, name: str) -> dict:
    """Full PVC description including conditions — useful for Pending/stuck-binding investigation."""
    p = v1.read_namespaced_persistent_volume_claim(name=name, namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    return {
        "name": p.metadata.name,
        "namespace": p.metadata.namespace,
        "phase": p.status.phase,
        "capacity": dict(p.status.capacity or {}),
        "requested": dict((p.spec.resources.requests or {}) if p.spec.resources else {}),
        "storage_class": p.spec.storage_class_name,
        "access_modes": p.spec.access_modes or [],
        "volume_name": p.spec.volume_name,
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (p.status.conditions or [])
        ],
        "labels": p.metadata.labels or {},
    }
