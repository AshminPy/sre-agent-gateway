"""
ConfigMap tools — config metadata for SRE investigation.

No Secret tool exists anywhere in this server (by design — see
PRODUCTION-LAUNCH-PLAN.md P4 acceptance: "no write/exec/port-forward").
ConfigMap *values* still pass through security.redact() (applied generically
by the @guarded decorator in server.py) in case a value looks secret-shaped
even though it isn't in a Secret object.
"""
from kubernetes import client

from security import REQUEST_TIMEOUT, MAX_TEXT_CHARS


def _trim_data(data: dict | None) -> dict:
    out = {}
    for k, v in (data or {}).items():
        if isinstance(v, str) and len(v) > MAX_TEXT_CHARS:
            v = v[:MAX_TEXT_CHARS] + f"...[truncated {len(v) - MAX_TEXT_CHARS} chars]"
        out[k] = v
    return out


def get_configmaps(v1: client.CoreV1Api, namespace: str) -> dict:
    """List ConfigMaps in namespace — names + key names only, not values."""
    items = v1.list_namespaced_config_map(namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    result = []
    for cm in items.items:
        result.append({
            "name": cm.metadata.name,
            "namespace": cm.metadata.namespace,
            "keys": sorted((cm.data or {}).keys()),
            "labels": cm.metadata.labels or {},
        })
    return {"configmaps": result, "count": len(result)}


def get_configmap(v1: client.CoreV1Api, namespace: str, name: str) -> dict:
    """Get a single ConfigMap's data — values redacted if secret-shaped, trimmed if large."""
    cm = v1.read_namespaced_config_map(name=name, namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    return {
        "name": cm.metadata.name,
        "namespace": cm.metadata.namespace,
        "data": _trim_data(cm.data),
        "binary_data_keys": sorted((cm.binary_data or {}).keys()),
        "labels": cm.metadata.labels or {},
        "annotations": cm.metadata.annotations or {},
    }
