from kubernetes import client

from security import REQUEST_TIMEOUT


def get_pods(v1: client.CoreV1Api, namespace: str) -> dict:
    """List all pods in namespace with status summary."""
    pods = v1.list_namespaced_pod(namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    result = []
    for pod in pods.items:
        container_statuses = []
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                last_state = {}
                if cs.last_state and cs.last_state.terminated:
                    t = cs.last_state.terminated
                    last_state = {
                        "exit_code": t.exit_code,
                        "reason": t.reason,
                        "finished_at": str(t.finished_at),
                    }
                container_statuses.append({
                    "name": cs.name,
                    "ready": cs.ready,
                    "restart_count": cs.restart_count,
                    "image": cs.image,
                    "last_state": last_state,
                })
        result.append({
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "phase": pod.status.phase,
            "conditions": [
                {"type": c.type, "status": c.status}
                for c in (pod.status.conditions or [])
            ],
            "container_statuses": container_statuses,
            "labels": pod.metadata.labels or {},
        })
    return {"pods": result, "count": len(result)}


def describe_pod(v1: client.CoreV1Api, namespace: str, pod_name: str) -> dict:
    """Full pod description including resource limits and termination reason."""
    pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace, _request_timeout=REQUEST_TIMEOUT)
    containers = []
    for c in pod.spec.containers:
        resources = {}
        if c.resources:
            resources = {
                "requests": {
                    "memory": c.resources.requests.get("memory") if c.resources.requests else None,
                    "cpu": c.resources.requests.get("cpu") if c.resources.requests else None,
                },
                "limits": {
                    "memory": c.resources.limits.get("memory") if c.resources.limits else None,
                    "cpu": c.resources.limits.get("cpu") if c.resources.limits else None,
                },
            }
        containers.append({
            "name": c.name,
            "image": c.image,
            "resources": resources,
            "command": c.command,
            "args": c.args,
            "volume_mounts": [
                {"name": vm.name, "mount_path": vm.mount_path, "read_only": bool(vm.read_only)}
                for vm in (c.volume_mounts or [])
            ],
        })

    container_statuses = []
    if pod.status.container_statuses:
        for cs in pod.status.container_statuses:
            last_state = {}
            if cs.last_state and cs.last_state.terminated:
                t = cs.last_state.terminated
                last_state = {
                    "exit_code": t.exit_code,
                    "reason": t.reason,
                    "message": t.message,
                    "finished_at": str(t.finished_at),
                    "started_at": str(t.started_at),
                }
            state = {}
            if cs.state:
                if cs.state.waiting:
                    state = {"phase": "waiting", "reason": cs.state.waiting.reason, "message": cs.state.waiting.message}
                elif cs.state.running:
                    state = {"phase": "running", "started_at": str(cs.state.running.started_at)}
                elif cs.state.terminated:
                    t = cs.state.terminated
                    state = {"phase": "terminated", "exit_code": t.exit_code, "reason": t.reason, "message": t.message}
            container_statuses.append({
                "name": cs.name,
                "ready": cs.ready,
                "restart_count": cs.restart_count,
                "image": cs.image,
                "state": state,
                "last_state": last_state,
            })

    volumes = [
        {"name": v.name, "source": _volume_source(v)}
        for v in (pod.spec.volumes or [])
    ]

    return {
        "name": pod.metadata.name,
        "namespace": pod.metadata.namespace,
        "phase": pod.status.phase,
        "node_name": pod.spec.node_name,
        "containers": containers,
        "container_statuses": container_statuses,
        "volumes": volumes,
        "labels": pod.metadata.labels or {},
        "annotations": pod.metadata.annotations or {},
        "owner_references": [
            {"kind": o.kind, "name": o.name} for o in (pod.metadata.owner_references or [])
        ],
    }


def _volume_source(volume) -> str:
    """Identify a pod volume's backing type without exposing secret values."""
    if volume.secret:
        return f"secret:{volume.secret.secret_name}"
    if volume.config_map:
        return f"configMap:{volume.config_map.name}"
    if volume.persistent_volume_claim:
        return f"pvc:{volume.persistent_volume_claim.claim_name}"
    if volume.empty_dir:
        return "emptyDir"
    if volume.host_path:
        return f"hostPath:{volume.host_path.path}"
    return "other"
