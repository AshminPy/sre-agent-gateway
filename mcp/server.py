"""
SRE Agent — Custom K8s MCP Server
Fallback MCP when GKE Remote MCP lacks coverage.
Auth: Workload Identity on GCP, kubeconfig locally, or GKE Fleet Connect
      Gateway (on-prem / non-GKE clusters — see docs/connect-gateway-onprem.md)
      via a kubeconfig context when K8S_MCP_KUBE_CONTEXT is set.
Transport: stateless streamable-http for Cloud Run

Read-only guarantee: every @mcp.tool() below is wrapped in security.guarded(),
which validates arguments, enforces namespace scope, rate-limits, redacts
secret-shaped values, trims oversized responses, and audit-logs every call —
see security.py. No tool in this file issues a create/patch/update/delete/
replace/exec/portforward call against the Kubernetes API; grep proof lives in
the P4 completion evidence.

NOTE on imports: every helper below is imported with an `_impl` suffix alias.
This is deliberate, not stylistic — several @mcp.tool() functions share their
exact name with the tools/*.py function they call (e.g. describe_deployment).
Without the alias, the module-level `def describe_deployment(...)` for the
tool would rebind the name `describe_deployment` in this module's globals,
and the call inside that same function body would resolve to itself at call
time (Python looks up globals at call time, not def time) — infinite
recursion. Aliasing avoids the trap entirely rather than relying on
call-order accidents.
"""
import os
import logging
import tempfile
from functools import lru_cache

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from fastmcp import FastMCP
from response_guard import ModelArmorResponseGuard
from security import guarded
from tools.pods import get_pods as _get_pods, describe_pod as _describe_pod
from tools.logs import get_pod_logs as _get_pod_logs, get_previous_pod_logs as _get_previous_pod_logs
from tools.events import get_events as _get_events, get_namespace_events as _get_namespace_events
from tools.deployments import (
    get_deployments as _get_deployments,
    describe_deployment as _describe_deployment,
    get_replicasets as _get_replicasets,
    describe_replicaset as _describe_replicaset,
)
from tools.workloads import (
    get_statefulsets as _get_statefulsets,
    describe_statefulset as _describe_statefulset,
    get_daemonsets as _get_daemonsets,
    describe_daemonset as _describe_daemonset,
)
from tools.services import (
    get_services as _get_services,
    describe_service as _describe_service,
    get_endpoints as _get_endpoints,
)
from tools.nodes import get_nodes as _get_nodes, describe_node as _describe_node
from tools.configmaps import get_configmaps as _get_configmaps, get_configmap as _get_configmap
from tools.scaling import get_hpas as _get_hpas, describe_hpa as _describe_hpa
from tools.storage import get_pvcs as _get_pvcs, describe_pvc as _describe_pvc
from tools.jobs import get_jobs as _get_jobs, describe_job as _describe_job

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

# POC (#203 follow-on, 2026-09-06): application-level Model Armor response
# sanitization — see response_guard.py's module docstring for why this exists
# and what CONTENT_AUTHZ gateway inspection still doesn't cover. One
# registration here covers every @mcp.tool() below; no per-tool changes.
mcp.add_middleware(ModelArmorResponseGuard())


@lru_cache(maxsize=1)
def get_k8s_clients():
    """
    Lazy K8s client — initializes on first tool call only.
    On GCP (direct GKE endpoint): uses Workload Identity + GCS CA cert.
    On-prem / non-GKE via Connect Gateway: uses a kubeconfig context
      (K8S_MCP_KUBE_CONTEXT) whose auth is the gke-gcloud-auth-plugin exec
      credential — see docs/connect-gateway-onprem.md. This is the same
      mechanism proven live against the sre-lab kind cluster in Task 4;
      Connect Gateway is reached AS a kubeconfig context, so this is just
      config.load_kube_config(context=...) instead of the default context.
    Locally without either: falls back to the default kubeconfig context.
    """
    from kubernetes import client, config
    from google.auth import default
    from google.auth.transport.requests import Request as GoogleAuthRequest

    endpoint = os.environ.get("GKE_CLUSTER_ENDPOINT", "")
    gcs_path = os.environ.get("GKE_CA_CERT_GCS_PATH", "")
    kube_context = os.environ.get("K8S_MCP_KUBE_CONTEXT", "").strip()

    if endpoint and gcs_path:
        # Running on GCP — use Workload Identity, direct GKE endpoint
        logger.info("Initializing K8s client via Workload Identity")
        try:
            credentials, _ = default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            credentials.refresh(GoogleAuthRequest())

            # Download CA cert from GCS
            from google.cloud import storage
            bucket_name = gcs_path.replace("gs://", "").split("/")[0]
            blob_name = "/".join(gcs_path.replace("gs://", "").split("/")[1:])
            gcs_client = storage.Client(credentials=credentials)
            blob = gcs_client.bucket(bucket_name).blob(blob_name)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
            blob.download_to_filename(tmp.name)
            ca_cert_path = tmp.name
            logger.info(f"CA cert downloaded from GCS to {ca_cert_path}")

            configuration = client.Configuration()
            configuration.host = endpoint
            configuration.verify_ssl = True
            configuration.ssl_ca_cert = ca_cert_path
            configuration.api_key = {"authorization": f"Bearer {credentials.token}"}
            configuration.api_key_prefix = {"authorization": ""}
            api_client = client.ApiClient(configuration)
            logger.info(f"K8s client ready → {endpoint}")
            return (
                client.CoreV1Api(api_client),
                client.AppsV1Api(api_client),
                client.BatchV1Api(api_client),
                client.AutoscalingV2Api(api_client),
            )

        except Exception as e:
            logger.error(f"Workload Identity init failed: {e}")
            raise

    elif kube_context:
        # Connect Gateway (or any other named context) — kubeconfig-based,
        # short-lived exec-plugin creds, no static token stored.
        logger.info(f"Initializing K8s client via kubeconfig context={kube_context}")
        config.load_kube_config(context=kube_context)
        api_client = client.ApiClient()
        return (
            client.CoreV1Api(api_client),
            client.AppsV1Api(api_client),
            client.BatchV1Api(api_client),
            client.AutoscalingV2Api(api_client),
        )

    else:
        # Local development — use kubeconfig's current-context
        logger.info("No GCP env vars found — using local kubeconfig default context")
        config.load_kube_config()
        api_client = client.ApiClient()
        return (
            client.CoreV1Api(api_client),
            client.AppsV1Api(api_client),
            client.BatchV1Api(api_client),
            client.AutoscalingV2Api(api_client),
        )


# ── Tools — Pod ───────────────────────────────────────────────────

@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_pods(namespace: str = "test-incidents") -> dict:
    """List all pods in namespace with phase, restart count, and last termination reason."""
    v1, _, _, _ = get_k8s_clients()
    return _get_pods(v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("pod_name",))
def describe_pod_detail(namespace: str, pod_name: str) -> dict:
    """Full pod description: resource limits, memory limits, termination reason, exit code."""
    v1, _, _, _ = get_k8s_clients()
    return _describe_pod(v1, namespace, pod_name)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("pod_name",))
def get_current_logs(
    namespace: str,
    pod_name: str,
    container: str | None = None,
    tail_lines: int = 100,
) -> dict:
    """Get current container logs. Use for CrashLoopBackOff app error evidence."""
    v1, _, _, _ = get_k8s_clients()
    return _get_pod_logs(v1, namespace, pod_name, container, tail_lines)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("pod_name",))
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
    v1, _, _, _ = get_k8s_clients()
    return _get_previous_pod_logs(v1, namespace, pod_name, container, tail_lines)


@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_events(namespace: str, pod_name: str | None = None) -> dict:
    """
    Get Kubernetes events. ImagePullBackOff pull errors appear here.
    Filter by pod_name to get events for a specific pod.
    """
    v1, _, _, _ = get_k8s_clients()
    return _get_events(v1, namespace, pod_name)


@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_namespace_events(namespace: str) -> dict:
    """All events in namespace, no pod filter — broader view for namespace-wide incidents."""
    v1, _, _, _ = get_k8s_clients()
    return _get_namespace_events(v1, namespace)


# ── Tools — Deployment / ReplicaSet ─────────────────────────────────

@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_deployments(namespace: str = "test-incidents") -> dict:
    """List deployments with replica status and conditions."""
    _, apps_v1, _, _ = get_k8s_clients()
    return _get_deployments(apps_v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("deployment_name",))
def describe_deployment(namespace: str, deployment_name: str) -> dict:
    """Full deployment description: strategy, resource requests/limits, rollout/change info."""
    _, apps_v1, _, _ = get_k8s_clients()
    return _describe_deployment(apps_v1, namespace, deployment_name)


@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_replicasets(namespace: str = "test-incidents") -> dict:
    """List ReplicaSets — rollout/change history evidence."""
    _, apps_v1, _, _ = get_k8s_clients()
    return _get_replicasets(apps_v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("replicaset_name",))
def describe_replicaset(namespace: str, replicaset_name: str) -> dict:
    """Full ReplicaSet description."""
    _, apps_v1, _, _ = get_k8s_clients()
    return _describe_replicaset(apps_v1, namespace, replicaset_name)


# ── Tools — StatefulSet / DaemonSet ──────────────────────────────────

@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_statefulsets(namespace: str = "test-incidents") -> dict:
    """List StatefulSets with replica status."""
    _, apps_v1, _, _ = get_k8s_clients()
    return _get_statefulsets(apps_v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("name",))
def describe_statefulset(namespace: str, name: str) -> dict:
    """Full StatefulSet description: resource requests/limits, rollout/update strategy."""
    _, apps_v1, _, _ = get_k8s_clients()
    return _describe_statefulset(apps_v1, namespace, name)


@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_daemonsets(namespace: str = "test-incidents") -> dict:
    """List DaemonSets with rollout status."""
    _, apps_v1, _, _ = get_k8s_clients()
    return _get_daemonsets(apps_v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("name",))
def describe_daemonset(namespace: str, name: str) -> dict:
    """Full DaemonSet description: resource requests/limits, rollout status."""
    _, apps_v1, _, _ = get_k8s_clients()
    return _describe_daemonset(apps_v1, namespace, name)


# ── Tools — Service / Connectivity ───────────────────────────────────

@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_services(namespace: str = "test-incidents") -> dict:
    """List Services with type, cluster IP, and ports."""
    v1, _, _, _ = get_k8s_clients()
    return _get_services(v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("service_name",))
def describe_service(namespace: str, service_name: str) -> dict:
    """Full Service description."""
    v1, _, _, _ = get_k8s_clients()
    return _describe_service(v1, namespace, service_name)


@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_endpoints(namespace: str = "test-incidents") -> dict:
    """List Endpoints — shows whether a Service has any healthy backing pods."""
    v1, _, _, _ = get_k8s_clients()
    return _get_endpoints(v1, namespace)


# ── Tools — Node / Scheduling (cluster-scoped) ───────────────────────

@mcp.tool()
@guarded()
def list_nodes() -> dict:
    """List cluster nodes with condition summary and capacity/allocatable. No namespace needed."""
    v1, _, _, _ = get_k8s_clients()
    return _get_nodes(v1)


@mcp.tool()
@guarded(name_fields=("node_name",))
def describe_node(node_name: str) -> dict:
    """Full node description: conditions, capacity/allocatable, taints, addresses."""
    v1, _, _, _ = get_k8s_clients()
    return _describe_node(v1, node_name)


# ── Tools — ConfigMap ─────────────────────────────────────────────────

@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_configmaps(namespace: str = "test-incidents") -> dict:
    """List ConfigMaps in namespace — names + key names only, not values."""
    v1, _, _, _ = get_k8s_clients()
    return _get_configmaps(v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("name",))
def get_configmap(namespace: str, name: str) -> dict:
    """Get a single ConfigMap's data — secret-shaped values redacted, large values trimmed."""
    v1, _, _, _ = get_k8s_clients()
    return _get_configmap(v1, namespace, name)


# ── Tools — HPA / Scaling ─────────────────────────────────────────────

@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_hpas(namespace: str = "test-incidents") -> dict:
    """List HPAs with current/desired/min/max replicas."""
    _, _, _, autoscaling_v2 = get_k8s_clients()
    return _get_hpas(autoscaling_v2, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("name",))
def describe_hpa(namespace: str, name: str) -> dict:
    """Full HPA description including per-metric current/target values and conditions."""
    _, _, _, autoscaling_v2 = get_k8s_clients()
    return _describe_hpa(autoscaling_v2, namespace, name)


# ── Tools — Storage ─────────────────────────────────────────────────

@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_pvcs(namespace: str = "test-incidents") -> dict:
    """List PVCs with bound status and capacity."""
    v1, _, _, _ = get_k8s_clients()
    return _get_pvcs(v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("name",))
def describe_pvc(namespace: str, name: str) -> dict:
    """Full PVC description including conditions — useful for Pending/stuck-binding investigation."""
    v1, _, _, _ = get_k8s_clients()
    return _describe_pvc(v1, namespace, name)


# ── Tools — Jobs ──────────────────────────────────────────────────────

@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_jobs(namespace: str = "test-incidents") -> dict:
    """List Jobs with completion status."""
    _, _, batch_v1, _ = get_k8s_clients()
    return _get_jobs(batch_v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("job_name",))
def describe_job(namespace: str, job_name: str) -> dict:
    """Full Job description including conditions and failure reason."""
    _, _, batch_v1, _ = get_k8s_clients()
    return _describe_job(batch_v1, namespace, job_name)


# ── Health checks ─────────────────────────────────────────────────────
# Plain Starlette routes, not MCP tools — not counted against the tool
# rate limit, not audit-logged as investigation evidence. Used by Cloud
# Run's startup/liveness probes and for manual smoke-testing.

@mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
async def healthz(request: Request) -> PlainTextResponse:
    """Liveness — process is up. Does not touch Kubernetes (must stay fast/cheap)."""
    return PlainTextResponse("ok")


@mcp.custom_route("/readyz", methods=["GET"], include_in_schema=False)
async def readyz(request: Request) -> JSONResponse:
    """Readiness — can this process actually reach the configured cluster."""
    try:
        v1, _, _, _ = get_k8s_clients()
        v1.list_namespace(limit=1, _request_timeout=(3, 5))
        return JSONResponse({"ready": True})
    except Exception as e:
        logger.warning(f"readyz check failed: {e}")
        return JSONResponse({"ready": False, "error": str(e)}, status_code=503)


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
