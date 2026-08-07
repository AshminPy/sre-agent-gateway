"""Job tools — batch workload evidence for SRE investigation."""
from kubernetes import client

from security import REQUEST_TIMEOUT


def get_jobs(batch_v1: client.BatchV1Api, namespace: str) -> dict:
    """List Jobs with completion status."""
    items = batch_v1.list_namespaced_job(namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    result = []
    for j in items.items:
        result.append({
            "name": j.metadata.name,
            "namespace": j.metadata.namespace,
            "active": j.status.active or 0,
            "succeeded": j.status.succeeded or 0,
            "failed": j.status.failed or 0,
            "completions": j.spec.completions,
            "parallelism": j.spec.parallelism,
            "start_time": str(j.status.start_time) if j.status.start_time else None,
            "completion_time": str(j.status.completion_time) if j.status.completion_time else None,
        })
    return {"jobs": result, "count": len(result)}


def describe_job(batch_v1: client.BatchV1Api, namespace: str, job_name: str) -> dict:
    """Full Job description including conditions and failure reason."""
    j = batch_v1.read_namespaced_job(name=job_name, namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    return {
        "name": j.metadata.name,
        "namespace": j.metadata.namespace,
        "active": j.status.active or 0,
        "succeeded": j.status.succeeded or 0,
        "failed": j.status.failed or 0,
        "completions": j.spec.completions,
        "parallelism": j.spec.parallelism,
        "backoff_limit": j.spec.backoff_limit,
        "start_time": str(j.status.start_time) if j.status.start_time else None,
        "completion_time": str(j.status.completion_time) if j.status.completion_time else None,
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (j.status.conditions or [])
        ],
        "labels": j.metadata.labels or {},
    }
