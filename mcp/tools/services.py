"""Service / Endpoints tools — connectivity evidence for SRE investigation."""
from kubernetes import client

from security import REQUEST_TIMEOUT


def get_services(v1: client.CoreV1Api, namespace: str) -> dict:
    """List Services with type, cluster IP, and ports."""
    items = v1.list_namespaced_service(namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    result = []
    for s in items.items:
        result.append({
            "name": s.metadata.name,
            "namespace": s.metadata.namespace,
            "type": s.spec.type,
            "cluster_ip": s.spec.cluster_ip,
            "ports": [
                {"name": p.name, "port": p.port, "target_port": str(p.target_port), "protocol": p.protocol}
                for p in (s.spec.ports or [])
            ],
            "selector": s.spec.selector or {},
            "labels": s.metadata.labels or {},
        })
    return {"services": result, "count": len(result)}


def describe_service(v1: client.CoreV1Api, namespace: str, service_name: str) -> dict:
    """Full Service description."""
    s = v1.read_namespaced_service(name=service_name, namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    return {
        "name": s.metadata.name,
        "namespace": s.metadata.namespace,
        "type": s.spec.type,
        "cluster_ip": s.spec.cluster_ip,
        "external_ips": s.spec.external_ips or [],
        "ports": [
            {"name": p.name, "port": p.port, "target_port": str(p.target_port), "protocol": p.protocol,
             "node_port": p.node_port}
            for p in (s.spec.ports or [])
        ],
        "selector": s.spec.selector or {},
        "session_affinity": s.spec.session_affinity,
        "labels": s.metadata.labels or {},
        "annotations": s.metadata.annotations or {},
    }


def get_endpoints(v1: client.CoreV1Api, namespace: str) -> dict:
    """List Endpoints — shows whether a Service has any healthy backing pods."""
    items = v1.list_namespaced_endpoints(namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    result = []
    for e in items.items:
        subsets = []
        for sub in (e.subsets or []):
            subsets.append({
                "addresses": [a.ip for a in (sub.addresses or [])],
                "not_ready_addresses": [a.ip for a in (sub.not_ready_addresses or [])],
                "ports": [{"name": p.name, "port": p.port, "protocol": p.protocol} for p in (sub.ports or [])],
            })
        result.append({
            "name": e.metadata.name,
            "namespace": e.metadata.namespace,
            "subsets": subsets,
            "has_ready_addresses": any(s["addresses"] for s in subsets),
        })
    return {"endpoints": result, "count": len(result)}
