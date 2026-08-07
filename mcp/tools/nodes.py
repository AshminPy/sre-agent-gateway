"""Node tools — cluster-scoped (not namespaced). Read-only, no cordon/drain/taint."""
from kubernetes import client

from security import REQUEST_TIMEOUT


def get_nodes(v1: client.CoreV1Api) -> dict:
    """List cluster nodes with condition summary and capacity/allocatable."""
    nodes = v1.list_node(_request_timeout=REQUEST_TIMEOUT)
    result = []
    for n in nodes.items:
        conditions = {c.type: c.status for c in (n.status.conditions or [])}
        result.append({
            "name": n.metadata.name,
            "conditions": conditions,
            "ready": conditions.get("Ready") == "True",
            "capacity": dict(n.status.capacity or {}),
            "allocatable": dict(n.status.allocatable or {}),
            "kubelet_version": n.status.node_info.kubelet_version if n.status.node_info else None,
            "labels": n.metadata.labels or {},
        })
    return {"nodes": result, "count": len(result)}


def describe_node(v1: client.CoreV1Api, node_name: str) -> dict:
    """Full node description: conditions, capacity/allocatable, taints, addresses."""
    n = v1.read_node(name=node_name, _request_timeout=REQUEST_TIMEOUT)
    return {
        "name": n.metadata.name,
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (n.status.conditions or [])
        ],
        "capacity": dict(n.status.capacity or {}),
        "allocatable": dict(n.status.allocatable or {}),
        "addresses": [{"type": a.type, "address": a.address} for a in (n.status.addresses or [])],
        "taints": [
            {"key": t.key, "value": t.value, "effect": t.effect} for t in (n.spec.taints or [])
        ],
        "unschedulable": bool(n.spec.unschedulable),
        "kubelet_version": n.status.node_info.kubelet_version if n.status.node_info else None,
        "os_image": n.status.node_info.os_image if n.status.node_info else None,
        "labels": n.metadata.labels or {},
        "annotations": n.metadata.annotations or {},
    }
