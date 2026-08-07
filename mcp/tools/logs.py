from kubernetes import client

from security import REQUEST_TIMEOUT


def get_pod_logs(
    v1: client.CoreV1Api,
    namespace: str,
    pod_name: str,
    container: str | None = None,
    tail_lines: int = 100,
) -> dict:
    """Get current pod logs — trimmed to tail_lines."""
    try:
        logs = v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container,
            tail_lines=tail_lines,
            timestamps=True,
            _request_timeout=REQUEST_TIMEOUT,
        )
        return {
            "pod": pod_name,
            "namespace": namespace,
            "container": container,
            "tail_lines": tail_lines,
            "logs": logs,
        }
    except Exception as e:
        return {
            "pod": pod_name,
            "namespace": namespace,
            "error": str(e),
            "logs": None,
        }


def get_previous_pod_logs(
    v1: client.CoreV1Api,
    namespace: str,
    pod_name: str,
    container: str | None = None,
    tail_lines: int = 100,
) -> dict:
    """Get logs from the PREVIOUS container run — critical for CrashLoopBackOff/OOMKilled."""
    try:
        logs = v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container,
            previous=True,          # ← this is the key flag
            tail_lines=tail_lines,
            timestamps=True,
            _request_timeout=REQUEST_TIMEOUT,
        )
        return {
            "pod": pod_name,
            "namespace": namespace,
            "container": container,
            "previous": True,
            "tail_lines": tail_lines,
            "logs": logs,
        }
    except Exception as e:
        return {
            "pod": pod_name,
            "namespace": namespace,
            "previous": True,
            "error": str(e),
            "logs": None,
        }
