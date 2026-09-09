"""
SRE Agent — Custom K8s MCP Server
Fallback MCP when GKE Remote MCP lacks coverage.
Auth: Workload Identity on GCP, kubeconfig locally, or GKE Fleet Connect
      Gateway (on-prem / non-GKE clusters — see docs/connect-gateway-onprem.md).
      One shared Cloud Run deployment now serves every "custom"-type cluster
      registered in clusters.json (CLUSTER_CONFIG_BUCKET) -- every K8s tool
      call requires an explicit cluster_id, resolved against that registry
      and connected via its own per-cluster kubeconfig context. See
      resolve_cluster()/get_k8s_clients(cluster_id) below and issue #86.
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
import json
import logging
import tempfile
import time

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from fastmcp import FastMCP
from response_guard import ModelArmorResponseGuard
from security import guarded, set_client_cache_invalidator, set_cluster_resolver
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


class ClusterNotFoundError(ValueError):
    """cluster_id does not exist in the registry, is disabled, or is not a
    'custom' cluster this server is authorized to serve. Caught by
    security.guarded() and turned into a structured {"error": ...} response,
    same as any other ValidationError -- never a code path that could pick a
    different cluster to keep the call alive."""


_CLUSTER_REGISTRY_CACHE: dict = {}
_CLUSTER_REGISTRY_LOADED_AT: float = 0.0
_CLUSTER_REGISTRY_TTL = 300.0  # 5 min -- matches agent/mcp_client.py's own TTL

# Section 2 correction (2026-09-08): distinguishes "no registry configured at all"
# (CLUSTER_CONFIG_BUCKET unset -- true local dev) from "a registry IS configured but
# could not be read this time" (a real deployed-environment outage: GCS unreachable,
# IAM revoked, malformed object, etc.) -- both used to collapse into the same empty
# {} with no way to tell them apart, which let resolve_cluster() treat a genuine
# outage as "no registry" and return a permissive placeholder that (via
# get_k8s_clients()'s own local-dev fallback) could silently connect ANY requested
# cluster_id to whatever K8S_MCP_KUBE_CONTEXT/default kubeconfig this process
# happens to have -- a different, wrong cluster in every real deployment.
_CLUSTER_REGISTRY_LOAD_FAILED: bool = False


def _load_cluster_registry() -> dict:
    """Load clusters.json from the SAME GCS bucket the agent itself reads
    (CLUSTER_CONFIG_BUCKET, granted via google_storage_bucket_iam_member
    .mcp_runtime_cluster_config_reader). Returns {} if the bucket env var is
    unset or the object can't be read -- callers must treat an empty
    registry as "no cluster is known", not "fall back to something". See
    resolve_cluster()'s docstring for why an unset bucket and a failed read
    are no longer treated the same way despite both landing here as {}.

    Deliberately a standalone copy of agent/mcp_client.py's
    _build_cluster_registry(), not an import from agent/ -- this module has
    no dependency on agent/ so the MCP image stays deployable standalone
    (same reasoning security.py's own docstring gives for being
    dependency-light).
    """
    global _CLUSTER_REGISTRY_LOAD_FAILED
    bucket_name = os.environ.get("CLUSTER_CONFIG_BUCKET", "").strip()
    if not bucket_name:
        _CLUSTER_REGISTRY_LOAD_FAILED = False
        return {}
    try:
        from google.cloud import storage as gcs
        blob = gcs.Client().bucket(bucket_name).blob("clusters.json")
        data = json.loads(blob.download_as_text())
    except Exception as exc:
        logger.error("cluster registry load failed (bucket=%s): %s", bucket_name, exc)
        _CLUSTER_REGISTRY_LOAD_FAILED = True
        return {}
    _CLUSTER_REGISTRY_LOAD_FAILED = False

    registry: dict = {}
    for c in data.get("clusters", []):
        name = (c.get("name") or "").strip()
        if not name:
            continue
        registry[name] = {
            "cluster_type":       (c.get("type") or "gke").lower(),
            "enabled":            bool(c.get("enabled", True)),
            "kube_context":       (c.get("kube_context") or "").strip(),
            # Dynamic Connect Gateway fields (added 2026-09-07): when both are
            # set, get_k8s_clients() builds the connection at runtime instead
            # of looking up a context in the static, image-baked
            # mcp/connect-gateway-kubeconfig.yaml -- this is what makes adding
            # a NEW on-prem cluster genuinely zero-touch (no image rebuild).
            # Empty (default) keeps existing entries (e.g. sre-lab) on the
            # already-live-validated static-kubeconfig path, unchanged.
            "fleet_project_number": (c.get("fleet_project_number") or "").strip(),
            "fleet_membership":     (c.get("fleet_membership") or "").strip() or name,
            "allowed_namespaces": [
                n.strip() for n in (c.get("allowed_namespaces") or [])
                if isinstance(n, str) and n.strip()
            ],
        }
    return registry


def _get_cluster_registry() -> dict:
    global _CLUSTER_REGISTRY_CACHE, _CLUSTER_REGISTRY_LOADED_AT
    if not _CLUSTER_REGISTRY_CACHE or (time.time() - _CLUSTER_REGISTRY_LOADED_AT) > _CLUSTER_REGISTRY_TTL:
        _CLUSTER_REGISTRY_CACHE = _load_cluster_registry()
        _CLUSTER_REGISTRY_LOADED_AT = time.time()
    return _CLUSTER_REGISTRY_CACHE


_NO_REGISTRY_PLACEHOLDER_ENTRY = {
    "cluster_type": "custom", "enabled": True, "kube_context": "", "allowed_namespaces": [],
}


def resolve_cluster(cluster_id: str) -> dict:
    """Validate cluster_id against the registry and return its entry.

    Raises ClusterNotFoundError for anything that isn't a known, enabled,
    'custom'-type cluster -- unknown name, disabled entry, or a 'gke' entry
    (those are served by GKE Remote MCP, never this server; accepting one
    here would let a caller redirect this process at a cluster it was never
    authorized to reach through this path). No branch in this function ever
    returns a DIFFERENT cluster than the one asked for.

    Exception: if NO registry is configured at all (CLUSTER_CONFIG_BUCKET
    unset -- local dev only, never true in a deployed environment), there is
    nothing to validate cluster_id against, so this returns a permissive
    placeholder entry instead of raising. Rejecting every call here would
    make get_k8s_clients()'s own local-dev fallback (K8S_MCP_KUBE_CONTEXT /
    default kubeconfig) unreachable, since guarded() calls this function
    before the tool body ever runs. This placeholder still requires
    cluster_id to be a non-empty string (checked below) -- it only skips the
    "is this a REAL registered cluster" check, not the "was one asked for
    at all" check.

    Section 2 correction (2026-09-08): that placeholder is ONLY for the
    "no bucket configured" case. If CLUSTER_CONFIG_BUCKET IS set (always true
    in a deployed environment) but the read failed this call --
    _CLUSTER_REGISTRY_LOAD_FAILED is True -- this raises instead. A registry
    outage in deployed multi-cluster mode must be a SAFE FAILURE naming the
    requested cluster_id, never a silent fall-through to a placeholder that
    could route the request to a different cluster than the one asked for.
    """
    if not cluster_id or not isinstance(cluster_id, str):
        raise ClusterNotFoundError("cluster_id is required and must be a non-empty string")
    registry = _get_cluster_registry()
    if not registry:
        if _CLUSTER_REGISTRY_LOAD_FAILED:
            raise ClusterNotFoundError(
                f"cluster_id '{cluster_id}' could not be validated -- the cluster "
                "registry is configured (CLUSTER_CONFIG_BUCKET is set) but is "
                "currently unreadable (see server logs for the underlying GCS "
                "error). Refusing to guess or fall back to a default cluster; "
                "retry once the registry is readable again."
            )
        return _NO_REGISTRY_PLACEHOLDER_ENTRY
    entry = registry.get(cluster_id.strip())
    if entry is None:
        raise ClusterNotFoundError(
            f"cluster_id '{cluster_id}' is not in the cluster registry "
            f"(known clusters: {sorted(registry.keys())})"
        )
    if not entry["enabled"]:
        raise ClusterNotFoundError(f"cluster_id '{cluster_id}' is registered but disabled")
    if entry["cluster_type"] != "custom":
        raise ClusterNotFoundError(
            f"cluster_id '{cluster_id}' is type='{entry['cluster_type']}' -- this server "
            "only serves 'custom' clusters; GKE clusters route through GKE Remote MCP"
        )
    return entry


# Wire this module's own resolve_cluster() into security.guarded() so every
# tool call's cluster_id (and its per-cluster namespace scope) is validated
# BEFORE the tool body runs, not just inside get_k8s_clients(cluster_id). See
# security.set_cluster_resolver()'s docstring for why this is a registered
# callback rather than security.py importing from this module.
set_cluster_resolver(resolve_cluster)


_K8S_CLIENT_CACHE: dict = {}
# Section 2 correction, revised again 2026-09-09 after live reproduction: the
# Workload-Identity and dynamic-Connect-Gateway branches below each bake ONE
# token string into `configuration.api_key` at construction time and never
# refresh it; a bare @lru_cache (the original implementation) would keep
# returning that same client -- and therefore that same, eventually-expired
# token -- for the rest of the process lifetime. A first fix used a fixed
# 45-minute TTL, assumed safely under a real token's ~60-minute lifetime --
# LIVE-REPRODUCED FALSE the next day: a real ACCESS_TOKEN_EXPIRED 401 from
# connectgateway.googleapis.com at ~35 minutes elapsed, evidence in
# gs://sreagent-t2-demo-evidence/run_20260909_043840_vwob/. A fixed TTL is a
# guess about a real token's lifetime, which this codebase does not control
# (Connect Gateway's own effective window, not a plain GCP OAuth token, may be
# shorter). Lowered to 20 min as a more conservative bound, AND -- the actual
# fix -- security.py's guarded() now reactively evicts a cluster's cached
# client the moment a real auth failure is observed (see
# set_client_cache_invalidator() below), so correctness no longer depends on
# guessing the right number at all: worst case is one failed call, self-healed
# for every call after it, regardless of the real token lifetime.
_K8S_CLIENT_TOKEN_TTL = 1200.0


def _build_k8s_clients(cluster_id: str):
    """
    Builds a fresh, per-cluster K8s client -- see get_k8s_clients() below for the
    TTL-cached wrapper every tool actually calls. Cluster isolation itself comes
    from cluster_id being resolved through resolve_cluster() -- an unknown,
    disabled, or non-'custom' id raises ClusterNotFoundError before any
    connection is attempted. There is no path from here to a DIFFERENT
    cluster than the one that was validated.

    cluster_id is resolved through resolve_cluster() -- an unknown,
    disabled, or non-'custom' id raises ClusterNotFoundError before any
    connection is attempted. There is no path from here to a DIFFERENT
    cluster than the one that was validated.

    On GCP (direct GKE endpoint): uses Workload Identity + GCS CA cert. Not
    currently exercised by any 'custom'-type registry entry -- kept for the
    scenario where a future custom cluster is reached this way rather than
    via Connect Gateway.
    On-prem / non-GKE via Connect Gateway: uses the registry entry's own
    kube_context, whose auth is the gke-gcloud-auth-plugin exec credential —
    see docs/connect-gateway-onprem.md.
    Local development ONLY (registry unreachable, e.g. no
    CLUSTER_CONFIG_BUCKET at all): falls back to K8S_MCP_KUBE_CONTEXT / the
    default kubeconfig context. Never used for a real registry-resolved
    request in a deployed environment.
    """
    from kubernetes import client, config
    from google.auth import default
    from google.auth.transport.requests import Request as GoogleAuthRequest

    registry = _get_cluster_registry()
    if registry:
        # A real registry loaded -- cluster_id MUST already have been validated
        # by resolve_cluster() (called from guarded() before this ever runs);
        # re-resolving here is cheap insurance against any future caller that
        # skips that step, not a second, different source of truth.
        entry = resolve_cluster(cluster_id)
        kube_context        = entry["kube_context"]
        fleet_project_number = entry.get("fleet_project_number", "")
        fleet_membership      = entry.get("fleet_membership", "") or cluster_id
        endpoint = ""
        gcs_path = ""
        if not kube_context and not fleet_project_number:
            # A validated, enabled, 'custom' cluster with neither a static
            # kube_context NOR fleet_project_number configured is a real
            # misconfiguration (missing Terraform field for this entry), not
            # "use whatever this process would otherwise default to". Failing
            # loudly here is the only choice consistent with "never fall back
            # to a different cluster" -- falling through to the
            # local-kubeconfig branch below would silently connect this
            # validated cluster_id to whatever context happens to be the
            # process's own local default, which is a different cluster in
            # every real deployment.
            raise ClusterNotFoundError(
                f"cluster_id '{cluster_id}' is registered and enabled but has neither "
                "kube_context nor fleet_project_number configured -- fix the Terraform "
                "registry entry, do not fall back to a default connection"
            )
    elif _CLUSTER_REGISTRY_LOAD_FAILED:
        # Section 2 correction (2026-09-08): matches resolve_cluster()'s own guard --
        # defense-in-depth in case this function is ever reached without guarded()
        # having already rejected the call. A registry outage must never fall through
        # to the local-dev branch below, which would silently connect this cluster_id
        # to whatever K8S_MCP_KUBE_CONTEXT/default kubeconfig this process has.
        raise ClusterNotFoundError(
            f"cluster_id '{cluster_id}' could not be validated -- the cluster registry "
            "is configured but currently unreadable; refusing to construct a client "
            "that could silently connect to the wrong cluster."
        )
    else:
        # No registry configured at all -- local-dev-only fallback, see
        # docstring above. Never reached in a deployed environment, which
        # always sets CLUSTER_CONFIG_BUCKET.
        endpoint = os.environ.get("GKE_CLUSTER_ENDPOINT", "")
        gcs_path = os.environ.get("GKE_CA_CERT_GCS_PATH", "")
        kube_context = os.environ.get("K8S_MCP_KUBE_CONTEXT", "").strip()
        fleet_project_number = ""
        fleet_membership = ""

    if endpoint and gcs_path:
        # Running on GCP — use Workload Identity, direct GKE endpoint
        logger.info("Initializing K8s client via Workload Identity (cluster_id=%s)", cluster_id)
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

    elif fleet_project_number:
        # Dynamic Connect Gateway (added 2026-09-07): builds the connection
        # entirely from registry data (fleet_project_number + fleet_membership)
        # at request time -- no static, image-baked kubeconfig context
        # involved. This is what makes adding a NEW on-prem cluster genuinely
        # zero-touch: a Terraform registry entry is enough, no image rebuild.
        #
        # Same auth mechanism the static-kubeconfig path already uses, just
        # constructed directly instead of via a kubectl exec plugin: verified
        # (WebSearch, 2026-09-07) that gke-gcloud-auth-plugin's own
        # DefaultCredentialsTokenProvider does nothing more than mint a plain
        # Application Default Credentials access token and hand it to kubectl
        # as a Bearer token -- identical to what google.auth.default() +
        # credentials.refresh() produces below.
        #
        # URL format `projects/{PROJECT_NUMBER}/locations/global/memberships/
        # {MEMBERSHIP}` matches Google's own documented Connect Gateway
        # membership resource path AND this repo's own already-live-validated
        # static kubeconfig (mcp/connect-gateway-kubeconfig.yaml, proven via
        # real kubectl calls -- see docs/connect-gateway-onprem.md) -- same
        # host this process already reaches successfully via the static path,
        # just built at runtime instead of read from a file.
        #
        # connectgateway.googleapis.com is a public Google API endpoint with a
        # publicly-trusted TLS certificate (not a private cluster endpoint),
        # so no custom CA cert is downloaded here, unlike the direct-GKE-
        # endpoint branch above.
        logger.info(
            "Initializing K8s client via dynamic Connect Gateway "
            "(cluster_id=%s, fleet_project_number=%s, fleet_membership=%s) — "
            "no static kubeconfig file involved",
            cluster_id, fleet_project_number, fleet_membership,
        )
        try:
            credentials, _ = default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            credentials.refresh(GoogleAuthRequest())
            host = (
                f"https://connectgateway.googleapis.com/v1/projects/{fleet_project_number}"
                f"/locations/global/memberships/{fleet_membership}"
            )
            configuration = client.Configuration()
            configuration.host = host
            configuration.verify_ssl = True
            configuration.api_key = {"authorization": f"Bearer {credentials.token}"}
            configuration.api_key_prefix = {"authorization": ""}
            api_client = client.ApiClient(configuration)
            logger.info(f"K8s client ready (dynamic Connect Gateway) → {host}")
            return (
                client.CoreV1Api(api_client),
                client.AppsV1Api(api_client),
                client.BatchV1Api(api_client),
                client.AutoscalingV2Api(api_client),
            )
        except Exception as e:
            logger.error(f"Dynamic Connect Gateway init failed: {e}")
            raise

    elif kube_context:
        # Connect Gateway (or any other named context) — kubeconfig-based,
        # short-lived exec-plugin creds, no static token stored. LEGACY path:
        # kept unchanged and untouched by the dynamic branch above so the
        # already-live-validated sre-lab connection is never put at risk by
        # this change -- see "preserve the existing working cluster
        # connection during migration" in the Section 5 correction. Migrate
        # an entry to the dynamic path above by setting fleet_project_number
        # in its registry entry; until that's done, this path is unchanged.
        logger.info(
            "Initializing K8s client via kubeconfig context=%s (cluster_id=%s)",
            kube_context, cluster_id,
        )
        config.load_kube_config(context=kube_context)
        api_client = client.ApiClient()
        return (
            client.CoreV1Api(api_client),
            client.AppsV1Api(api_client),
            client.BatchV1Api(api_client),
            client.AutoscalingV2Api(api_client),
        )

    else:
        # Local development ONLY (no registry, no legacy env vars either) —
        # use kubeconfig's current-context. Never reached in a deployed
        # environment.
        logger.info(
            "No cluster registry and no GCP env vars found — using local "
            "kubeconfig default context (cluster_id=%s ignored)", cluster_id,
        )
        config.load_kube_config()
        api_client = client.ApiClient()
        return (
            client.CoreV1Api(api_client),
            client.AppsV1Api(api_client),
            client.BatchV1Api(api_client),
            client.AutoscalingV2Api(api_client),
        )


def get_k8s_clients(cluster_id: str):
    """TTL-cached wrapper around _build_k8s_clients() -- every tool calls THIS, never
    _build_k8s_clients() directly. Section 2 correction (2026-09-08): replaces the
    previous bare @lru_cache(maxsize=32), which had no expiry at all -- see
    _K8S_CLIENT_TOKEN_TTL's comment above for why that let a cached client
    permanently retain an expired bearer token. Per-cluster keying (issue #86) is
    unchanged: distinct cluster_ids never share or evict each other's entry.

    A failed build (ClusterNotFoundError or any other exception) is never cached --
    the next call retries _build_k8s_clients() fully, same as @lru_cache's own
    behavior of not memoizing exceptions.
    """
    cached = _K8S_CLIENT_CACHE.get(cluster_id)
    if cached is not None:
        clients, created_at = cached
        if time.time() - created_at < _K8S_CLIENT_TOKEN_TTL:
            return clients
    clients = _build_k8s_clients(cluster_id)
    _K8S_CLIENT_CACHE[cluster_id] = (clients, time.time())
    return clients


def _get_k8s_clients_cache_clear() -> None:
    _K8S_CLIENT_CACHE.clear()


get_k8s_clients.cache_clear = _get_k8s_clients_cache_clear


def _evict_k8s_client(cluster_id: str) -> None:
    """Evicts ONLY this cluster's cached client -- registered with security.py's
    guarded() so a real auth failure self-heals on the very next call for that
    cluster, without disturbing any other cluster's still-healthy cached client."""
    _K8S_CLIENT_CACHE.pop(cluster_id, None)


set_client_cache_invalidator(_evict_k8s_client)


# ── Tools — Pod ───────────────────────────────────────────────────

@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_pods(cluster_id: str, namespace: str = "test-incidents") -> dict:
    """List all pods in namespace with phase, restart count, and last termination reason."""
    v1, _, _, _ = get_k8s_clients(cluster_id)
    return _get_pods(v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("pod_name",))
def describe_pod_detail(cluster_id: str, namespace: str, pod_name: str) -> dict:
    """Full pod description: resource limits, memory limits, termination reason, exit code."""
    v1, _, _, _ = get_k8s_clients(cluster_id)
    return _describe_pod(v1, namespace, pod_name)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("pod_name",))
def get_current_logs(
    cluster_id: str,
    namespace: str,
    pod_name: str,
    container: str | None = None,
    tail_lines: int = 100,
) -> dict:
    """Get current container logs. Use for CrashLoopBackOff app error evidence."""
    v1, _, _, _ = get_k8s_clients(cluster_id)
    return _get_pod_logs(v1, namespace, pod_name, container, tail_lines)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("pod_name",))
def get_previous_logs(
    cluster_id: str,
    namespace: str,
    pod_name: str,
    container: str | None = None,
    tail_lines: int = 100,
) -> dict:
    """
    Get logs from the PREVIOUS crashed container.
    Critical for OOMKilled and CrashLoopBackOff — shows what happened before crash.
    """
    v1, _, _, _ = get_k8s_clients(cluster_id)
    return _get_previous_pod_logs(v1, namespace, pod_name, container, tail_lines)


@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_events(cluster_id: str, namespace: str, pod_name: str | None = None) -> dict:
    """
    Get Kubernetes events. ImagePullBackOff pull errors appear here.
    Filter by pod_name to get events for a specific pod.
    """
    v1, _, _, _ = get_k8s_clients(cluster_id)
    return _get_events(v1, namespace, pod_name)


@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_namespace_events(cluster_id: str, namespace: str) -> dict:
    """All events in namespace, no pod filter — broader view for namespace-wide incidents."""
    v1, _, _, _ = get_k8s_clients(cluster_id)
    return _get_namespace_events(v1, namespace)


# ── Tools — Deployment / ReplicaSet ─────────────────────────────────

@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_deployments(cluster_id: str, namespace: str = "test-incidents") -> dict:
    """List deployments with replica status and conditions."""
    _, apps_v1, _, _ = get_k8s_clients(cluster_id)
    return _get_deployments(apps_v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("deployment_name",))
def describe_deployment(cluster_id: str, namespace: str, deployment_name: str) -> dict:
    """Full deployment description: strategy, resource requests/limits, rollout/change info."""
    _, apps_v1, _, _ = get_k8s_clients(cluster_id)
    return _describe_deployment(apps_v1, namespace, deployment_name)


@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_replicasets(cluster_id: str, namespace: str = "test-incidents") -> dict:
    """List ReplicaSets — rollout/change history evidence."""
    _, apps_v1, _, _ = get_k8s_clients(cluster_id)
    return _get_replicasets(apps_v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("replicaset_name",))
def describe_replicaset(cluster_id: str, namespace: str, replicaset_name: str) -> dict:
    """Full ReplicaSet description."""
    _, apps_v1, _, _ = get_k8s_clients(cluster_id)
    return _describe_replicaset(apps_v1, namespace, replicaset_name)


# ── Tools — StatefulSet / DaemonSet ──────────────────────────────────

@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_statefulsets(cluster_id: str, namespace: str = "test-incidents") -> dict:
    """List StatefulSets with replica status."""
    _, apps_v1, _, _ = get_k8s_clients(cluster_id)
    return _get_statefulsets(apps_v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("name",))
def describe_statefulset(cluster_id: str, namespace: str, name: str) -> dict:
    """Full StatefulSet description: resource requests/limits, rollout/update strategy."""
    _, apps_v1, _, _ = get_k8s_clients(cluster_id)
    return _describe_statefulset(apps_v1, namespace, name)


@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_daemonsets(cluster_id: str, namespace: str = "test-incidents") -> dict:
    """List DaemonSets with rollout status."""
    _, apps_v1, _, _ = get_k8s_clients(cluster_id)
    return _get_daemonsets(apps_v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("name",))
def describe_daemonset(cluster_id: str, namespace: str, name: str) -> dict:
    """Full DaemonSet description: resource requests/limits, rollout status."""
    _, apps_v1, _, _ = get_k8s_clients(cluster_id)
    return _describe_daemonset(apps_v1, namespace, name)


# ── Tools — Service / Connectivity ───────────────────────────────────

@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_services(cluster_id: str, namespace: str = "test-incidents") -> dict:
    """List Services with type, cluster IP, and ports."""
    v1, _, _, _ = get_k8s_clients(cluster_id)
    return _get_services(v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("service_name",))
def describe_service(cluster_id: str, namespace: str, service_name: str) -> dict:
    """Full Service description."""
    v1, _, _, _ = get_k8s_clients(cluster_id)
    return _describe_service(v1, namespace, service_name)


@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_endpoints(cluster_id: str, namespace: str = "test-incidents") -> dict:
    """List Endpoints — shows whether a Service has any healthy backing pods."""
    v1, _, _, _ = get_k8s_clients(cluster_id)
    return _get_endpoints(v1, namespace)


# ── Tools — Node / Scheduling (cluster-scoped) ───────────────────────

@mcp.tool()
@guarded()
def list_nodes(cluster_id: str) -> dict:
    """List cluster nodes with condition summary and capacity/allocatable. No namespace needed."""
    v1, _, _, _ = get_k8s_clients(cluster_id)
    return _get_nodes(v1)


@mcp.tool()
@guarded(name_fields=("node_name",))
def describe_node(cluster_id: str, node_name: str) -> dict:
    """Full node description: conditions, capacity/allocatable, taints, addresses."""
    v1, _, _, _ = get_k8s_clients(cluster_id)
    return _describe_node(v1, node_name)


# ── Tools — ConfigMap ─────────────────────────────────────────────────

@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_configmaps(cluster_id: str, namespace: str = "test-incidents") -> dict:
    """List ConfigMaps in namespace — names + key names only, not values."""
    v1, _, _, _ = get_k8s_clients(cluster_id)
    return _get_configmaps(v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("name",))
def get_configmap(cluster_id: str, namespace: str, name: str) -> dict:
    """Get a single ConfigMap's data — secret-shaped values redacted, large values trimmed."""
    v1, _, _, _ = get_k8s_clients(cluster_id)
    return _get_configmap(v1, namespace, name)


# ── Tools — HPA / Scaling ─────────────────────────────────────────────

@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_hpas(cluster_id: str, namespace: str = "test-incidents") -> dict:
    """List HPAs with current/desired/min/max replicas."""
    _, _, _, autoscaling_v2 = get_k8s_clients(cluster_id)
    return _get_hpas(autoscaling_v2, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("name",))
def describe_hpa(cluster_id: str, namespace: str, name: str) -> dict:
    """Full HPA description including per-metric current/target values and conditions."""
    _, _, _, autoscaling_v2 = get_k8s_clients(cluster_id)
    return _describe_hpa(autoscaling_v2, namespace, name)


# ── Tools — Storage ─────────────────────────────────────────────────

@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_pvcs(cluster_id: str, namespace: str = "test-incidents") -> dict:
    """List PVCs with bound status and capacity."""
    v1, _, _, _ = get_k8s_clients(cluster_id)
    return _get_pvcs(v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("name",))
def describe_pvc(cluster_id: str, namespace: str, name: str) -> dict:
    """Full PVC description including conditions — useful for Pending/stuck-binding investigation."""
    v1, _, _, _ = get_k8s_clients(cluster_id)
    return _describe_pvc(v1, namespace, name)


# ── Tools — Jobs ──────────────────────────────────────────────────────

@mcp.tool()
@guarded(namespace_fields=("namespace",))
def list_jobs(cluster_id: str, namespace: str = "test-incidents") -> dict:
    """List Jobs with completion status."""
    _, _, batch_v1, _ = get_k8s_clients(cluster_id)
    return _get_jobs(batch_v1, namespace)


@mcp.tool()
@guarded(namespace_fields=("namespace",), name_fields=("job_name",))
def describe_job(cluster_id: str, namespace: str, job_name: str) -> dict:
    """Full Job description including conditions and failure reason."""
    _, _, batch_v1, _ = get_k8s_clients(cluster_id)
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
    """Readiness — can this process reach ITS OWN configuration.

    Section 5 redesign: there is no longer one "the configured cluster" --
    this process serves whichever cluster_id a caller names, resolved
    per-request against the registry. Actually connecting to and listing
    namespaces on some ARBITRARY cluster here (e.g. "the first one in the
    registry") would answer the wrong question (one cluster's reachability
    says nothing about any other's) and cost a real, unnecessary API call on
    every load-balancer health check. Instead this verifies the thing that
    actually gates every real request: can the registry itself be loaded
    (or, in the local-dev-only fallback, is a usable local kubeconfig
    context available) -- the same precondition guarded()/resolve_cluster()
    check before any tool runs.
    """
    try:
        registry = _get_cluster_registry()
        if registry:
            return JSONResponse({"ready": True, "clusters_registered": len(registry)})
        # No registry configured -- local-dev-only fallback path. Confirm the
        # legacy single-context config is at least loadable, without making a
        # real API call (that would defeat the point of a fast/cheap probe).
        from kubernetes import config
        kube_context = os.environ.get("K8S_MCP_KUBE_CONTEXT", "").strip()
        config.load_kube_config(context=kube_context or None)
        return JSONResponse({"ready": True, "clusters_registered": 0, "mode": "local-dev-fallback"})
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
