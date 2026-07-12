"""
SRE Agent — Custom K8s MCP Server
Fallback MCP when GKE Remote MCP lacks coverage.
Auth: Workload Identity on GCP, kubeconfig locally.
Transport: stateless streamable-http for Cloud Run
"""
import os
import logging
import tempfile
from functools import lru_cache

from fastmcp import FastMCP
from tools.pods import get_pods, describe_pod
from tools.logs import get_pod_logs, get_previous_pod_logs
from tools.events import get_events
from tools.deployments import get_deployments

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sre-mcp")

# ── FastMCP server — NO init at module level ──────────────────────
# Server starts immediately and binds to port.
# K8s client is initialized ONLY when first tool is called.
mcp = FastMCP(
    name="sre-k8s-mcp",
    instructions=(
        "Read-only Kubernetes investigation tools for SRE incident analysis. "
        "Use these tools to gather evidence about pod failures, logs, and events. "
        "Never modify any Kubernetes resource."
    ),
)


@lru_cache(maxsize=1)
def get_k8s_clients():
    """
    Lazy K8s client — initializes on first tool call only.
    On GCP: uses Workload Identity + GCS CA cert.
    Locally: falls back to kubeconfig.
    """
    from kubernetes import client, config
    from google.auth import default
    from google.auth.transport.requests import Request

    endpoint = os.environ.get("GKE_CLUSTER_ENDPOINT", "")
    gcs_path = os.environ.get("GKE_CA_CERT_GCS_PATH", "")

    if endpoint and gcs_path:
        # Running on GCP — use Workload Identity
        logger.info("Initializing K8s client via Workload Identity")
        try:
            credentials, _ = default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            credentials.refresh(Request())

            # Download CA cert from GCS
            from google.cloud import storage
            bucket_name = gcs_path.replace("gs://", "").split("/")[0]
            blob_name   = "/".join(gcs_path.replace("gs://", "").split("/")[1:])
            gcs_client  = storage.Client(credentials=credentials)
            blob        = gcs_client.bucket(bucket_name).blob(blob_name)
            tmp         = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
            blob.download_to_filename(tmp.name)
            ca_cert_path = tmp.name
            logger.info(f"CA cert downloaded from GCS to {ca_cert_path}")

            configuration = client.Configuration()
            configuration.host         = endpoint
            configuration.verify_ssl   = True
            configuration.ssl_ca_cert  = ca_cert_path
            configuration.api_key      = {"authorization": f"Bearer {credentials.token}"}
            configuration.api_key_prefix = {"authorization": ""}
            api_client = client.ApiClient(configuration)
            logger.info(f"K8s client ready → {endpoint}")
            return client.CoreV1Api(api_client), client.AppsV1Api(api_client)

        except Exception as e:
            logger.error(f"Workload Identity init failed: {e}")
            raise

    else:
        # Local development — use kubeconfig
        logger.info("No GCP env vars found — using local kubeconfig")
        config.load_kube_config()
        api_client = client.ApiClient()
        return client.CoreV1Api(api_client), client.AppsV1Api(api_client)


# ── Tools ─────────────────────────────────────────────────────────

@mcp.tool()
def list_pods(namespace: str = "test-incidents") -> dict:
    """List all pods in namespace with phase, restart count, and last termination reason."""
    v1, _ = get_k8s_clients()
    return get_pods(v1, namespace)


@mcp.tool()
def describe_pod_detail(namespace: str, pod_name: str) -> dict:
    """Full pod description: resource limits, memory limits, termination reason, exit code."""
    v1, _ = get_k8s_clients()
    return describe_pod(v1, namespace, pod_name)


@mcp.tool()
def get_current_logs(
    namespace: str,
    pod_name: str,
    container: str | None = None,
    tail_lines: int = 100,
) -> dict:
    """Get current container logs. Use for CrashLoopBackOff app error evidence."""
    v1, _ = get_k8s_clients()
    return get_pod_logs(v1, namespace, pod_name, container, tail_lines)


@mcp.tool()
def get_previous_logs(
    namespace: str,
    pod_name: str,
    container: str | None = None,
    tail_lines: int = 100,
) -> dict:
    """
    Get logs from the PREVIOUS crashed container.
    Critical for OOMKilled and CrashLoopBackOff — shows what happened before crash.
    """
    v1, _ = get_k8s_clients()
    return get_previous_pod_logs(v1, namespace, pod_name, container, tail_lines)


@mcp.tool()
def list_events(namespace: str, pod_name: str | None = None) -> dict:
    """
    Get Kubernetes events. ImagePullBackOff pull errors appear here.
    Filter by pod_name to get events for a specific pod.
    """
    v1, _ = get_k8s_clients()
    return get_events(v1, namespace, pod_name)


@mcp.tool()
def list_deployments(namespace: str = "test-incidents") -> dict:
    """List deployments with replica status and conditions."""
    _, apps_v1 = get_k8s_clients()
    return get_deployments(apps_v1, namespace)


# ── Entrypoint ────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    logger.info(f"Starting SRE K8s MCP server on port {port}")
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=port,
        path="/mcp",
        stateless_http=True,
    )
