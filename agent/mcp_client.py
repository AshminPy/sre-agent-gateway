"""
MCP Client — multi-cluster, dual-source routing.

Primary:  GKE Remote MCP (Google-managed, Preview/Pre-GA)
          URL: https://container.googleapis.com/mcp/read-only
          Auth: Bearer access token (NOT identity token)
          ALL tools require: parent = projects/{project}/locations/{location}/clusters/{cluster}

Fallback: Custom FastMCP on Cloud Run
          URL: from env K8S_MCP_URL
          Auth: Bearer identity token

Source: https://cloud.google.com/kubernetes-engine/docs/reference/mcp
"""
import importlib
import json
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

import httpx
import google.auth
import google.auth.transport.requests
import google.oauth2.id_token

log = logging.getLogger("sre-agent.mcp")

# ── Tool Allowlist ────────────────────────────────────────────────
# GKE Remote MCP — confirmed tools from official docs
GKE_REMOTE_TOOLS = frozenset({
    "list_k8s_events",       # kubectl events equivalent
    "describe_k8s_resource", # kubectl describe equivalent
    "get_k8s_resource",      # kubectl get -o yaml equivalent
    "get_k8s_logs",          # kubectl logs equivalent
    "list_k8s_api_resources", # kubectl api-resources equivalent
    "get_k8s_cluster_info",  # cluster info
})

# Custom K8s MCP tools (Cloud Run fallback) — read-only, no exec/write/secret values
CUSTOM_K8S_TOOLS = frozenset({
    # Pod
    "list_pods",
    "describe_pod_detail",
    "get_current_logs",
    "get_previous_logs",
    "list_events",

    # Deployment / ReplicaSet
    "list_deployments",
    "describe_deployment",
    "list_replicasets",
    "describe_replicaset",

    # StatefulSet / DaemonSet
    "list_statefulsets",
    "describe_statefulset",
    "list_daemonsets",
    "describe_daemonset",

    # Service / Connectivity
    "list_services",
    "describe_service",
    "list_endpoints",

    # Node / Scheduling
    "list_nodes",
    "describe_node",

    # ConfigMap
    "list_configmaps",
    "get_configmap",

    # HPA / Scaling
    "list_hpas",
    "describe_hpa",

    # Storage
    "list_pvcs",
    "describe_pvc",

    # Namespace-level events (broader view)
    "list_namespace_events",

    # Jobs
    "list_jobs",
    "describe_job",
})

def _additional_source_tools() -> frozenset:
    """Section 6: approved_tools from every catalog entry, regardless of
    enabled/disabled -- the allowlist itself is static and safe to include
    unconditionally; agent/source_catalog.py's own enabled/allowed_clusters
    checks (in select_additional_source()) are what actually gate whether a
    call is ever constructed in the first place, not this list."""
    from agent.source_catalog import SOURCE_CATALOG
    tools = set()
    for entry in SOURCE_CATALOG.values():
        tools |= set(entry.get("approved_tools", ()))
    return frozenset(tools)


ALLOWED_TOOLS = GKE_REMOTE_TOOLS | CUSTOM_K8S_TOOLS | _additional_source_tools()

# issue #72: which custom MCP tools (mcp/server.py) actually accept a pod_name param --
# confirmed against each tool's real @guarded(name_fields=...) signature, not assumed.
# The GKE-Remote-failure fallback path used to force-include pod_name in fallback_args
# for EVERY target, even ones like list_pods/list_nodes that don't take it at all.
_CUSTOM_TOOLS_ACCEPTING_POD_NAME = frozenset({
    "describe_pod_detail", "get_current_logs", "get_previous_logs", "list_events",
})

# issue #246: cluster-scoped custom MCP tools take no `namespace` param at all --
# a Kubernetes Node isn't namespaced. Confirmed against every CUSTOM_K8S_TOOLS
# function signature in mcp/server.py: these are the only two lacking `namespace`
# (every other one of the 24 remaining tools has it, several with a default).
# mcp_router.py's auto-fill used to setdefault("namespace", ...) unconditionally
# for every k8s_mcp call, so list_nodes/describe_node were rejected by the MCP
# server's own Pydantic validation ("unexpected_keyword_argument"). Same
# frozenset-exclusion pattern as _CUSTOM_TOOLS_ACCEPTING_POD_NAME above, not a
# schema fetch, for the same reason: agent/ doesn't load mcp/tool_spec.json at
# runtime, and hardcoding the two-tool exception list is smaller and safer than
# adding that dependency for two known, stable tool names.
_CUSTOM_TOOLS_WITHOUT_NAMESPACE = frozenset({
    "list_nodes", "describe_node",
})

# Write-style actions — always blocked
BLOCKED_ACTIONS = frozenset({
    "delete", "create", "patch", "update", "apply",
    "exec", "port-forward", "scale", "rollout",
})

# ── MCP Source Registry ───────────────────────────────────────────
MCP_REGISTRY = {
    "gke_remote_mcp": {
        # 2026-08-25: diagnostic run and reverted, same session (PR #193).
        # Real result: forcing this path to fail correctly fell through to
        # the custom-MCP fallback (CI's own smoke test: primary_mcp_source=
        # gke_remote_mcp, actual_mcp_sources=[k8s_mcp], smoke test passed).
        # The fallback's traffic DID appear in the Agent Gateway's own log
        # (TLS-intercepted, IAP-governed, ALLOWED) -- corrects an earlier
        # same-session assumption that custom MCP bypasses the gateway
        # entirely; it does not, it had simply never been exercised before.
        # Model Armor floor settings did NOT inspect it (0 GOOGLE_MCP_SERVER
        # entries in the same window, vs 12 real VERTEX_AI entries) --
        # confirmed empirically, matching the Model Armor API's own schema
        # (integratedServices only allows AI_PLATFORM / GOOGLE_MCP_SERVER,
        # no custom option).
        "url":         "https://container.googleapis.com/mcp/read-only",
        "auth":        "access_token",
        "description": "Google-managed GKE Remote MCP — read-only K8s investigation",
        "tools":       list(GKE_REMOTE_TOOLS),
        "ga_status":   "Preview/Pre-GA",
    },
    "k8s_mcp": {
        "url":         os.environ.get("K8S_MCP_URL", ""),
        "auth":        "identity_token",
        "description": "Custom read-only K8s MCP — pod logs, events, describe",
        "tools":       list(CUSTOM_K8S_TOOLS),
        "ga_status":   "GA",
    },
}

# ── Cluster Registry — GCS-backed, no redeploy needed ────────────
#
# Source of truth: gs://<CLUSTER_CONFIG_BUCKET>/clusters.json
#
# Schema:
#   {
#     "clusters": [
#       {
#         "name":               "prod-cluster-us-east1",  # canonical ID — invoke payload
#                                                          # "cluster" field must match this
#                                                          # exactly, or one of "aliases" below
#         "aliases":            ["prod-east", "prod-1"],   # optional — approved alternate
#                                                          # names this cluster may be routed
#                                                          # by (see resolve_cluster_routing)
#         "project":            "prj-n-multitenant-0868",
#         "region":              "us-east1",
#         "type":                "gke",                    # "gke" | "custom"
#         "environment":         "production",              # optional — free text, used by
#                                                          # the project/env/namespace routing
#                                                          # tier
#         "allowed_namespaces": ["team-a", "team-b"],       # optional — namespaces this
#                                                          # cluster owns; empty/omitted means
#                                                          # "no declared ownership" and this
#                                                          # cluster is skipped by the
#                                                          # namespace-based routing tier
#                                                          # (it never disambiguates by
#                                                          # namespace alone)
#         "owner":               "team-a-sre",              # optional — free text, audit only
#         "enabled":             true                       # optional, default true — disabled
#                                                          # clusters are never auto-routed to,
#                                                          # even by exact id/alias match
#       }
#     ]
#   }
#
# To add a cluster: set var.additional_clusters in Terraform and apply
# (iac/agent/variables.tf) — Terraform is the sole source of truth for this
# file; a manual edit in GCS will be silently overwritten on the next apply.
# Agent SA needs: roles/storage.objectViewer on the config bucket.

def _build_cluster_registry() -> dict:
    """Load cluster list from GCS config file. Fails loudly if misconfigured."""
    bucket = os.environ.get("CLUSTER_CONFIG_BUCKET", "")
    if bucket:
        try:
            from google.cloud import storage as gcs
            blob = gcs.Client().bucket(bucket).blob("clusters.json")
            data = json.loads(blob.download_as_text())
            registry: dict = {}
            for c in data.get("clusters", []):
                name = c.get("name", "").strip()
                if not name:
                    continue
                cluster_type = c.get("type", "gke").lower()
                aliases = [
                    a.strip() for a in (c.get("aliases") or [])
                    if isinstance(a, str) and a.strip()
                ]
                allowed_namespaces = [
                    n.strip() for n in (c.get("allowed_namespaces") or [])
                    if isinstance(n, str) and n.strip()
                ]
                registry[name] = {
                    "canonical_id": name,
                    "aliases":      aliases,
                    "project":      (c.get("project") or os.environ.get("PROJECT_ID", "")).strip(),
                    "region":       (c.get("region") or "us-east1").strip(),
                    "cluster_type": cluster_type,
                    "environment":  (c.get("environment") or "unknown").strip(),
                    "allowed_namespaces": allowed_namespaces,
                    "owner":        (c.get("owner") or "").strip(),
                    "enabled":      bool(c.get("enabled", True)),
                    "mcp_primary":  "gke_remote_mcp" if cluster_type == "gke" else "k8s_mcp",
                    "mcp_fallback": "k8s_mcp"        if cluster_type == "gke" else "gke_remote_mcp",
                    "mcp_url":      c.get("mcp_url", os.environ.get("K8S_MCP_URL", "")),
                }
            if registry:
                log.info("Cluster registry loaded from GCS: %d cluster(s)", len(registry))
                return registry
            log.error("clusters.json loaded but contains no valid entries — agent has no clusters")
        except Exception as exc:
            log.error("Failed to load cluster registry from GCS bucket=%s: %s", bucket, exc)

    # No GCS bucket configured — fail loudly, no silent fallback to test cluster
    log.error(
        "CLUSTER_CONFIG_BUCKET env var not set and no GCS registry loaded. "
        "The agent has no cluster targets. Set CLUSTER_CONFIG_BUCKET and ensure "
        "clusters.json exists in that bucket."
    )
    return {}


_REGISTRY_CACHE: dict = {}
_REGISTRY_LOADED_AT: float = 0.0
_REGISTRY_TTL = 300.0  # 5-minute TTL — picks up clusters.json updates without redeploy


def _get_cluster_registry() -> dict:
    """Return the cluster registry, rebuilding from GCS if the TTL has expired."""
    global _REGISTRY_CACHE, _REGISTRY_LOADED_AT
    if not _REGISTRY_CACHE or (time.time() - _REGISTRY_LOADED_AT) > _REGISTRY_TTL:
        _REGISTRY_CACHE = _build_cluster_registry()
        _REGISTRY_LOADED_AT = time.time()
    return _REGISTRY_CACHE


def _build_parent(project: str, region: str, cluster: str) -> str:
    """Build GKE Remote MCP parent resource path."""
    return f"projects/{project}/locations/{region}/clusters/{cluster}"


def _get_access_token() -> str:
    """Get GCP access token via Workload Identity / ADC (primary auth).

    Workload Identity Federation is the only supported method inside Agent Engine.
    gcloud is not installed in the Agent Engine runtime — ADC is the correct path.
    """
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


def _get_identity_token(url: str) -> str:
    """Get identity token for Cloud Run MCP via Workload Identity / ADC."""
    return google.oauth2.id_token.fetch_id_token(
        google.auth.transport.requests.Request(), url
    )


def _validate_tool(tool_name: str, arguments: Dict[str, Any]) -> Optional[str]:
    """Returns error string if blocked, None if allowed."""
    if tool_name not in ALLOWED_TOOLS:
        return f"Tool '{tool_name}' not in allowlist"
    for blocked in BLOCKED_ACTIONS:
        if blocked in tool_name.lower():
            return f"Tool '{tool_name}' contains blocked action '{blocked}'"
    return None


def _build_gke_args(
    tool_name: str,
    arguments: Dict[str, Any],
    parent: str,
    namespace: str,
    pod_name: str,
) -> Dict[str, Any]:
    """
    Build correct args for GKE Remote MCP tools.
    ALL tools require 'parent'. Other args vary per tool.
    Based on: https://cloud.google.com/kubernetes-engine/docs/reference/mcp
    """
    base = {"parent": parent}

    if tool_name == "list_k8s_events":
        # ListK8SEventsRequest: parent(req), name(opt), namespace(opt),
        #                       resourceType(opt), allNamespaces(opt), limit(opt)
        if namespace:
            base["namespace"] = namespace
        if pod_name:
            base["name"] = pod_name
            base["resourceType"] = "pod"
        return base

    elif tool_name == "describe_k8s_resource":
        # DescribeK8SResourceRequest: parent(req), resourceType(req), name(req),
        #                             namespace(opt)
        base["resourceType"] = arguments.get("resourceType", "pod")
        base["name"]         = arguments.get("name", pod_name)
        if namespace:
            base["namespace"] = namespace
        return base

    elif tool_name == "get_k8s_resource":
        # GetK8SResourceRequest: parent(req), resourceType(req), name(req),
        #                        namespace(opt), outputFormat(opt)
        base["resourceType"] = arguments.get("resourceType", "pod")
        base["name"]         = arguments.get("name", pod_name)
        if namespace:
            base["namespace"] = namespace
        return base

    elif tool_name == "get_k8s_logs":
        # GetK8SLogsRequest: parent(req), name(req), namespace(opt),
        #                    container(opt), previous(opt), tail(opt, string-encoded int)
        if namespace:
            base["namespace"] = namespace
        if pod_name:
            base["name"] = pod_name        # API uses "name", not "podName"
        # issue #211: container was never forwarded, even when the model supplied
        # one -- on a multi-container pod (any Istio/Envoy/Linkerd-sidecar-injected
        # workload) this silently lost the one piece of information that decides
        # whether the logs pulled are the app's or the sidecar's.
        if arguments.get("container"):
            base["container"] = arguments["container"]
        if arguments.get("previous"):
            base["previous"] = True
        tail = arguments.get("tailLines", arguments.get("tail", 100))
        base["tail"] = str(tail)           # API requires string, not int
        return base

    elif tool_name == "list_k8s_api_resources":
        # ListK8SAPIResourcesRequest: parent(req)
        return base  # only parent needed

    elif tool_name == "get_k8s_cluster_info":
        # GetK8SClusterInfoRequest: parent(req)
        return base

    # Default — pass parent + any extra args
    return {**base, **{k: v for k, v in arguments.items()
                       if k not in ("namespace", "pod_name", "name", "resourceType")}}


def _map_to_custom_tool(gke_tool: str) -> Optional[str]:
    """Map GKE Remote MCP tool → equivalent custom K8s MCP tool.

    issue #72: list_k8s_api_resources (lists available K8s API resource TYPES, e.g.
    `kubectl api-resources`) and get_k8s_cluster_info (cluster metadata, no
    namespace/pod involved) used to both map to list_pods -- semantically invalid,
    and list_pods doesn't even accept the pod_name arg the fallback path force-sent.
    No real equivalent exists in CUSTOM_K8S_TOOLS for either -- omitted entirely so
    the fallback attempt is correctly skipped (see the `fallback_tool` check at each
    call site) and the real error surfaces, instead of silently returning pod data
    for an unrelated query.
    """
    mapping = {
        "list_k8s_events":       "list_events",
        "describe_k8s_resource": "describe_pod_detail",
        "get_k8s_resource":      "describe_pod_detail",
        "get_k8s_logs":          "get_current_logs",
    }
    return mapping.get(gke_tool)


def _is_not_found_result(content: Any) -> bool:
    """issue #70: GKE Remote MCP returns HTTP 200 with the underlying kubectl-style
    error TEXT embedded in the result body for a not-found resource -- not a structured
    error field, not a non-200 status. Confirmed via a real E2E failure (docs/testing/
    e2e-honest-baseline-2026-08-09-notification-relay.md):
    {"output": "Error from server (NotFound): Pod \"notification-relay\" not found"}.
    """
    text = content if isinstance(content, str) else json.dumps(content)
    return "notfound" in text.lower()


# Model Armor replaces a blocked response body with this notice, in-band, over a
# normal HTTP 200. Exact string observed in run_20260826_211251_rlwe's ev_002:
#   "Model Armor: Response violates content security configurations.
#    However, the operation was successful."
MODEL_ARMOR_BLOCK_MARKERS = ("model armor", "violates content security")


def _is_model_armor_blocked_result(content: Any) -> bool:
    """True when Model Armor replaced the real tool output with its block notice.

    Same failure shape as _is_not_found_result above: HTTP 200, non-empty body,
    but the body is NOT the data that was asked for. Detected 2026-08-26 --
    Model Armor's pi_and_jailbreak filter matched (MEDIUM_AND_ABOVE) on a
    describe_k8s_resource response and, with the floor setting running
    inspect_and_block=true, the pod spec was discarded and replaced with the
    notice. call_tool returned ok=True, the notice was stored as ev_002, and the
    LLM -- with no pod spec but a prompt saying the pod "cannot pull its image"
    -- fabricated an image name, an error string, and a root cause, citing an
    unrelated evidence_id.

    Both markers must be present. "model armor" alone would match legitimate
    output from a cluster that happens to run a workload with that name.
    """
    text = content if isinstance(content, str) else json.dumps(content)
    lowered = text.lower()
    return all(marker in lowered for marker in MODEL_ARMOR_BLOCK_MARKERS)


# docs/management/CURRENT-STATE.md: "custom/fallback MCP traffic is not Model
# Armor-inspected". Confirmed root cause -- iac/agent/model_armor.tf's
# google_model_armor_floorsetting.mcp only supports integrated_services =
# ["GOOGLE_MCP_SERVER", "AI_PLATFORM"] (the Model Armor API's own schema has no
# "custom Cloud Run MCP" option), so the GKE Remote MCP path is covered by that
# project-level floor setting but the custom/self-hosted k8s_mcp path never was
# and never can be, at the infra layer. This closes the gap at the app layer
# instead, reusing the exact same sanitize helper (SREAgent._sanitize) that
# agent/main.py already uses for query()'s input/output sanitize calls -- same
# Model Armor template, same client, no new API surface.
#
# Response-only, deliberately: this mirrors what the GKE Remote floor setting
# itself already demonstrably inspects (_is_model_armor_blocked_result above
# was written against a real RESPONSE-side block). Local import to match this
# module's existing avoidance of importing agent.main at call_tool() import
# time -- agent.main has no module-level dependency back on agent.mcp_client,
# so this is not a real cycle, just kept lazy for consistency with main.py's
# own "no module-level code that can fail" policy.
def _sanitize_custom_mcp_response(
    content: Any, tool_name: str, mcp_source: str, cluster_name: str,
) -> Optional[str]:
    """Runs the custom-MCP response through Model Armor. Returns an error string
    if Model Armor flagged it (caller should fail the call), or None if clean."""
    from agent.main import SREAgent

    text = content if isinstance(content, str) else json.dumps(content)
    _sanitized, blocked = SREAgent._sanitize(text, is_output=True)
    if not blocked:
        return None
    log.error(
        "call_tool: MODEL_ARMOR_BLOCKED (custom MCP) tool=%s mcp_source=%s cluster=%s -- "
        "app-layer sanitize flagged the response; treating as a failed call so no "
        "evidence is written and completeness scoring reflects the gap.",
        tool_name, mcp_source, cluster_name,
    )
    return (
        "MODEL_ARMOR_BLOCKED: custom-MCP response failed Model Armor sanitize "
        "inspection; the requested data was withheld"
    )


def _try_custom_mcp_fallback(
    is_gke_remote: bool,
    cluster_info: dict,
    tool_name: str,
    namespace: str,
    pod_name: str,
    run_id: str,
    cluster_name: str,
) -> Optional[Dict[str, Any]]:
    """issue #72: centralizes the GKE-Remote-failure fallback so it fires from EVERY
    real failure mode (non-200 status, empty/unparseable response, network exception)
    -- previously only the non-200 status branch attempted it, contradicting the
    module's own documented "Auto-falls-back to custom K8s MCP if GKE Remote fails."
    Also only includes pod_name in the fallback call's args when the target tool
    actually accepts it (_CUSTOM_TOOLS_ACCEPTING_POD_NAME) -- it used to be
    force-included for every target, even ones like list_pods that don't take it.

    Returns the fallback call's result dict, or None if no fallback applies (not a
    GKE Remote call, no valid mapped equivalent tool, or no fallback source
    configured) -- callers fall through to their own error result in that case.
    """
    if not is_gke_remote:
        return None
    fallback      = cluster_info.get("mcp_fallback", "k8s_mcp")
    fallback_tool = _map_to_custom_tool(tool_name)
    if not (fallback and fallback_tool and fallback_tool in CUSTOM_K8S_TOOLS):
        return None
    log.info("call_tool: GKE Remote failed → fallback %s.%s", fallback, fallback_tool)
    # issue #246: same guard as mcp_router.py's auto-fill -- no current mapping
    # above targets a cluster-scoped tool (list_nodes/describe_node), but keep
    # this consistent with the rest of the module so a future mapping addition
    # can't silently reintroduce the same "unexpected_keyword_argument" failure.
    # Section 5 redesign: the custom MCP server now requires cluster_id on every
    # call (see mcp/server.py's resolve_cluster()) -- this fallback targets the
    # SAME cluster the GKE Remote call was already resolved to, never a
    # different one, matching mcp_router.py's own forced (not setdefault)
    # cluster_id assignment for the primary k8s_mcp routing path.
    fallback_args: Dict[str, Any] = {"cluster_id": cluster_name}
    if fallback_tool not in _CUSTOM_TOOLS_WITHOUT_NAMESPACE:
        fallback_args["namespace"] = namespace
    if pod_name and fallback_tool in _CUSTOM_TOOLS_ACCEPTING_POD_NAME:
        fallback_args["pod_name"] = pod_name
    return call_tool(fallback, fallback_tool, fallback_args, run_id, cluster_name)


def _call_catalog_source(
    mcp_source: str, tool_name: str, arguments: Dict[str, Any], cluster_name: str,
) -> Dict[str, Any]:
    """Section 6 dispatch target for any agent/source_catalog.py entry. Fully
    generic by design: dynamically imports the entry's own `output_adapter`
    module and calls the function named after the tool. Adding a future
    source (Elastic, Grafana, git MCP) means a new catalog entry + a new
    adapter module -- this function never changes, never hardcodes a source
    name, and never needs a new branch. Each adapter module is responsible
    for its own read-only-ness, bounded query limits, and normalized evidence
    shape; this function's only job is a safe, structured call + a uniform
    ok/error result shape matching what tool_executor.py/evidence_extractor.py
    already expect from any tool call, regardless of source.
    """
    from agent.source_catalog import SOURCE_CATALOG
    start = time.time()
    entry = SOURCE_CATALOG.get(mcp_source, {})
    if tool_name not in entry.get("approved_tools", frozenset()):
        return {
            "ok": False, "error": f"tool '{tool_name}' is not an approved tool for source '{mcp_source}'",
            "tool": tool_name, "mcp_source": mcp_source, "duration_s": round(time.time() - start, 2),
        }
    module_path = entry.get("output_adapter")
    try:
        adapter = importlib.import_module(module_path)
        fn = getattr(adapter, tool_name)
    except (ImportError, AttributeError) as exc:
        log.warning(
            "call_tool catalog source=%s tool=%s: adapter dispatch failed: %s",
            mcp_source, tool_name, exc,
        )
        return {
            "ok": False, "error": f"adapter dispatch failed: {exc}",
            "tool": tool_name, "mcp_source": mcp_source, "duration_s": round(time.time() - start, 2),
        }
    try:
        call_args = {k: v for k, v in arguments.items() if k != "cluster_id"}
        result = fn(cluster_id=cluster_name, **call_args)
        return {
            "ok": True, "result": result, "tool": tool_name,
            "mcp_source": mcp_source, "duration_s": round(time.time() - start, 2),
        }
    except Exception as exc:
        # Matches every other source's contract: a failure is a clean, honest
        # {"ok": False, "error": ...} result -- never a fabricated success and
        # never an unhandled exception reaching tool_executor.py.
        log.warning("call_tool catalog source=%s tool=%s failed: %s", mcp_source, tool_name, exc)
        return {
            "ok": False, "error": str(exc), "tool": tool_name,
            "mcp_source": mcp_source, "duration_s": round(time.time() - start, 2),
        }


def call_tool(
    mcp_source: str,
    tool_name: str,
    arguments: Dict[str, Any],
    run_id: str = "",
    cluster_name: str = "",
    _broadened_retry: bool = False,
) -> Dict[str, Any]:
    """
    Call one tool on one MCP source.
    Validates allowlist before any network call.
    Builds correct args for GKE Remote MCP (parent field required).
    Auto-falls-back to custom K8s MCP if GKE Remote fails.
    _broadened_retry (issue #70, internal use only): set on the recursive call made
    when a name-scoped list_k8s_events returns NotFound, to prevent retrying twice.
    """
    validation_error = _validate_tool(tool_name, arguments)
    if validation_error:
        log.warning("call_tool BLOCKED: %s", validation_error)
        return {
            "ok": False, "error": f"BLOCKED: {validation_error}",
            "tool": tool_name, "mcp_source": mcp_source,
            "duration_s": 0, "blocked": True,
        }

    # Section 6: a catalog source (e.g. "prometheus") is dispatched to its own
    # narrow adapter, never the generic JSON-RPC-over-HTTP path below -- these
    # sources speak their OWN real API (Prometheus's own REST endpoints), not
    # our MCP wire format, so they can't share call_tool()'s HTTP/JSON-RPC
    # machinery the way gke_remote_mcp/k8s_mcp (both genuine MCP servers) do.
    from agent.source_catalog import SOURCE_CATALOG
    if mcp_source in SOURCE_CATALOG:
        return _call_catalog_source(mcp_source, tool_name, arguments, cluster_name)

    cluster_info  = _get_cluster_registry().get(cluster_name, {})
    source_config = MCP_REGISTRY.get(mcp_source, {})
    is_gke_remote = (mcp_source == "gke_remote_mcp")
    # Always defined (not just on the GKE-remote branch below) -- _try_custom_mcp_fallback
    # is called from every failure path regardless of which branch ran, and short-circuits
    # via is_gke_remote before ever reading these on the custom-MCP path.
    namespace = ""
    pod_name  = ""

    if is_gke_remote:
        url     = source_config.get("url", "https://container.googleapis.com/mcp/read-only")
        project = cluster_info.get("project", os.environ.get("PROJECT_ID", "your-gcp-project-id"))
        region  = cluster_info.get("region",  os.environ.get("CLUSTER_1_REGION", "us-central1"))
        parent  = _build_parent(project, region, cluster_name)

        # Extract namespace and pod from arguments
        namespace = (arguments.get("namespace") or
                     cluster_info.get("namespace", ""))
        pod_name  = (arguments.get("name") or
                     arguments.get("pod_name") or
                     arguments.get("pod") or "")

        # Build correct args with parent field
        args  = _build_gke_args(tool_name, arguments, parent, namespace, pod_name)
        token = _get_access_token()

        log.info(
            "call_tool gke_remote source=%s tool=%s parent=%s args=%s",
            mcp_source, tool_name, parent, args,
        )

    else:
        # Custom K8s MCP
        url = cluster_info.get("mcp_url") or source_config.get("url", "")
        if not url:
            fallback = cluster_info.get("mcp_fallback", "k8s_mcp")
            if fallback and fallback != mcp_source:
                log.warning("call_tool: no URL for %s, trying %s", mcp_source, fallback)
                return call_tool(fallback, tool_name, arguments, run_id, cluster_name)
            return {
                "ok": False, "error": f"No URL for {mcp_source}",
                "tool": tool_name, "mcp_source": mcp_source, "duration_s": 0,
            }
        args  = dict(arguments)
        token = _get_identity_token(url)

        log.info(
            "call_tool custom source=%s tool=%s args=%s",
            mcp_source, tool_name, args,
        )

    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method":  "tools/call",
        "params":  {"name": tool_name, "arguments": args},
    }

    # GKE Remote MCP endpoint (no /mcp suffix — it IS the endpoint)
    endpoint = url if is_gke_remote else f"{url}/mcp"

    start = time.time()
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                endpoint,
                json=payload,
                headers={
                    "Content-Type":  "application/json",
                    "Accept":        "application/json, text/event-stream",
                    "Authorization": f"Bearer {token}",
                },
            )
        duration = round(time.time() - start, 2)

        if resp.status_code != 200:
            if resp.status_code == 401:
                error_msg = (
                    f"HTTP 401: token rejected by {mcp_source}. "
                    f"For GKE Remote MCP: ensure ADC has container.googleapis.com scope. "
                    f"For custom MCP on Cloud Run: grant roles/run.invoker to the agent SA. "
                    f"Body: {resp.text[:150]}"
                )
            else:
                error_msg = f"HTTP {resp.status_code}: {resp.text[:300]}"
            log.warning(
                "call_tool FAILED source=%s tool=%s: %s",
                mcp_source, tool_name, error_msg[:200],
            )

            fallback_result = _try_custom_mcp_fallback(
                is_gke_remote, cluster_info, tool_name, namespace, pod_name, run_id, cluster_name,
            )
            if fallback_result is not None:
                return fallback_result

            return {
                "ok": False, "error": error_msg,
                "tool": tool_name, "mcp_source": mcp_source, "duration_s": duration,
            }

        # Parse SSE or direct JSON response
        content, protocol_error, is_tool_error = _parse_response(resp.text)

        # MCP protocol error (unknown tool, invalid arguments, server error).
        # Surface the server's own message instead of the old generic
        # "Empty response" -- see _rpc_error_message. Fallback is still attempted
        # first, since a different MCP source may accept the same call.
        if protocol_error is not None:
            log.warning(
                "call_tool: %s tool=%s mcp_source=%s cluster=%s",
                protocol_error, tool_name, mcp_source, cluster_name,
            )
            fallback_result = _try_custom_mcp_fallback(
                is_gke_remote, cluster_info, tool_name, namespace, pod_name, run_id, cluster_name,
            )
            # A fallback that SUCCEEDS is the answer. A fallback that FAILS must not
            # overwrite the original diagnostic with its own: caught by this fix's own
            # test, where a real "-32602 Invalid arguments" was replaced by the far less
            # useful "No URL for k8s_mcp" -- losing the message all over again, which is
            # the exact thing this change exists to prevent. Keep both, primary first.
            if fallback_result is not None:
                if fallback_result.get("ok"):
                    return fallback_result
                fallback_result["error"] = (
                    f"{protocol_error} (fallback also failed: "
                    f"{fallback_result.get('error', 'unknown')})"
                )
                return fallback_result
            return {
                "ok": False, "error": protocol_error,
                "tool": tool_name, "mcp_source": mcp_source, "duration_s": duration,
            }

        # MCP tool execution error: `isError: true`. The body is an error message
        # sitting exactly where real data normally sits. This is a FAILED call --
        # returning ok=True here is what let an error string become evidence.
        # Deliberately checked BEFORE the NotFound/broadened-retry branch below:
        # that retry exists for a name-scoped miss, not for a tool that reported
        # its own failure, and retrying an isError result only burns latency.
        if is_tool_error:
            detail = content if isinstance(content, str) else json.dumps(content)
            log.error(
                "call_tool: MCP_TOOL_ERROR tool=%s mcp_source=%s cluster=%s -- server set "
                "isError=true; the body is an error message, not the requested data. "
                "Treating as a failed call so it cannot become evidence.",
                tool_name, mcp_source, cluster_name,
            )
            return {
                "ok": False,
                "error": f"MCP_TOOL_ERROR: {detail[:500]}",
                "tool": tool_name, "mcp_source": mcp_source, "duration_s": duration,
            }

        if content is not None:
            # issue #70: a name-scoped lookup returning NotFound is not proof the
            # resource doesn't exist -- the caller-supplied name hint itself can be
            # wrong (real E2E failure: a Deployment-name hint that didn't match the
            # actual generated pod name, both Pod and ReplicaSet lookups 404'd, and
            # the agent wrongly concluded "workload doesn't exist"). list_k8s_events
            # is the one GKE Remote MCP tool whose name/namespace filters are BOTH
            # optional (describe_k8s_resource/get_k8s_resource REQUIRE name per the
            # MCP schema -- can't be broadened the same way, see _build_gke_args).
            # Retry once, unscoped within the same namespace, before accepting
            # NotFound as the final answer.
            if (
                is_gke_remote and not _broadened_retry
                and tool_name == "list_k8s_events" and pod_name
                and _is_not_found_result(content)
            ):
                log.info(
                    "call_tool: list_k8s_events name=%s returned NotFound -- retrying "
                    "unscoped (namespace-wide) before concluding non-existence",
                    pod_name,
                )
                broadened = call_tool(
                    mcp_source, tool_name,
                    {k: v for k, v in arguments.items() if k not in ("name", "pod_name", "pod")},
                    run_id, cluster_name, _broadened_retry=True,
                )
                broadened["broadened_after_not_found"] = pod_name
                return broadened

            # cascading-001 root cause (2026-08-31): describe_k8s_resource and
            # get_k8s_resource are the two GKE Remote MCP tools whose `name` field is
            # REQUIRED (toolspec.json) -- unlike list_k8s_events above, there is no
            # unscoped/broadened retry available for them (_build_gke_args has no path
            # that drops `name` for either tool). A NotFound response here is therefore
            # NOT proof the resource doesn't exist, for exactly the same reason issue
            # #70 documented for list_k8s_events: the caller-supplied name can be a
            # guess that doesn't match the real generated name (a Deployment's pods and
            # ReplicaSet both carry a random hash suffix no caller can know in advance).
            #
            # Confirmed root cause of cascading-001's wrong RCA ("The order-api
            # Deployment and its ReplicaSet are missing from the cluster") --
            # order-api was running the entire time; a guessed exact name 404'd on
            # describe_k8s_resource/get_k8s_resource, and because this branch didn't
            # exist, that NotFound text fell through to `ok: True` below and became
            # "evidence" the RCA-builder LLM cited as proof of absence. Marking it
            # ok=False (same treatment as the isError branch above) keeps it out of
            # rca_builder's usable_evidence_ids, so a wrong name-guess can no longer by
            # itself ground a "resource is missing" claim -- while list_k8s_events
            # (which DOES retry broadened) or a correctly-named lookup can still ground
            # a real one.
            if (
                is_gke_remote
                and tool_name in ("describe_k8s_resource", "get_k8s_resource")
                and _is_not_found_result(content)
            ):
                looked_up_name = args.get("name", "")
                log.warning(
                    "call_tool: NAME_SCOPED_NOT_FOUND tool=%s name=%s mcp_source=%s "
                    "cluster=%s -- NotFound on a required-name lookup is not proof the "
                    "resource doesn't exist (the name may be a guess); no broadened "
                    "retry exists for this tool. Treating as a failed call so it cannot "
                    "be cited as evidence of absence.",
                    tool_name, looked_up_name, mcp_source, cluster_name,
                )
                return {
                    "ok": False,
                    "error": (
                        f"NAME_SCOPED_NOT_FOUND: server returned NotFound for "
                        f"{tool_name}(name={looked_up_name!r}). This does not prove the "
                        "resource doesn't exist -- the name may not match the real "
                        "generated name (pods/ReplicaSets carry a random suffix). "
                        "Confirm via list_k8s_events (unscoped) or a corrected name "
                        "before concluding absence."
                    ),
                    "tool": tool_name, "mcp_source": mcp_source, "duration_s": duration,
                }

            # Custom/fallback MCP (k8s_mcp) has no infra-level Model Armor coverage --
            # see _sanitize_custom_mcp_response's docstring/comment. GKE Remote MCP is
            # deliberately excluded here: it is already covered by the native floor
            # setting (google_model_armor_floorsetting.mcp, GOOGLE_MCP_SERVER), and
            # running this too would double-inspect the same response.
            if not is_gke_remote:
                armor_error = _sanitize_custom_mcp_response(
                    content, tool_name, mcp_source, cluster_name,
                )
                if armor_error is not None:
                    return {
                        "ok": False,
                        "error": armor_error,
                        "blocked": True,
                        "blocked_by": "model_armor",
                        "tool": tool_name, "mcp_source": mcp_source, "duration_s": duration,
                    }

            # A Model Armor block is a FAILED call, not a successful one. The real
            # payload is gone; only the block notice came back. Returning ok=True
            # here is what let a fabricated RCA be produced (see
            # _is_model_armor_blocked_result). Deliberately NOT routed through
            # _try_custom_mcp_fallback: the same floor setting is project-wide and
            # would sanitize the fallback's response identically, so retrying only
            # burns latency. Fail loudly and let the caller record the gap.
            if _is_model_armor_blocked_result(content):
                log.error(
                    "call_tool: MODEL_ARMOR_BLOCKED tool=%s mcp_source=%s cluster=%s -- "
                    "response body replaced by Model Armor's block notice, real payload "
                    "discarded. Treating as a failed call so no evidence is written and "
                    "completeness scoring reflects the gap.",
                    tool_name, mcp_source, cluster_name,
                )
                return {
                    "ok": False,
                    "error": (
                        "MODEL_ARMOR_BLOCKED: response body was replaced by Model Armor's "
                        "block notice; the requested data was never received"
                    ),
                    "blocked": True,
                    "blocked_by": "model_armor",
                    "tool": tool_name, "mcp_source": mcp_source, "duration_s": duration,
                }

            return {
                "ok": True, "result": content,
                "tool": tool_name, "mcp_source": mcp_source, "duration_s": duration,
            }

        # issue #72: empty/unparseable responses used to return this error directly with
        # no fallback attempt, contradicting the documented fallback behavior -- only a
        # non-200 HTTP status ever triggered it before.
        fallback_result = _try_custom_mcp_fallback(
            is_gke_remote, cluster_info, tool_name, namespace, pod_name, run_id, cluster_name,
        )
        if fallback_result is not None:
            return fallback_result

        return {
            "ok": False, "error": "Empty response",
            "tool": tool_name, "mcp_source": mcp_source, "duration_s": duration,
        }

    except Exception as e:
        # issue #72: network-level exceptions (timeout, connection refused, DNS failure,
        # etc.) used to return the error directly with no fallback attempt either.
        fallback_result = _try_custom_mcp_fallback(
            is_gke_remote, cluster_info, tool_name, namespace, pod_name, run_id, cluster_name,
        )
        if fallback_result is not None:
            return fallback_result

        return {
            "ok": False, "error": str(e),
            "tool": tool_name, "mcp_source": mcp_source,
            "duration_s": round(time.time() - start, 2),
        }


def _is_nonempty(value: Any) -> bool:
    """True unless value is None or an empty list/dict/string."""
    if value is None:
        return False
    if isinstance(value, (list, dict, str)):
        return len(value) > 0
    return True


def _extract_content(result: Dict[str, Any]) -> Optional[Any]:
    """
    Pull real (non-empty) content out of a JSON-RPC `result` dict.

    A `result` whose only content/structuredContent is `[]`, `{}`, or ""
    does NOT count as real content — returns None so the caller can keep
    scanning later SSE frames instead of treating it as a false-positive
    success.
    """
    structured = result.get("structuredContent")
    if _is_nonempty(structured):
        return structured

    content = result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "text":
            try:
                return json.loads(first["text"])
            except Exception:
                return first["text"]
        return content
    if _is_nonempty(content):
        return content

    # No real content/structuredContent — fall back to any other non-empty
    # field the result may carry (tools that don't wrap output in content/
    # structuredContent at all).
    other = {k: v for k, v in result.items() if k not in ("content", "structuredContent")}
    return other or None


def _rpc_error_message(data: Dict[str, Any]) -> Optional[str]:
    """Extract a JSON-RPC 2.0 protocol error message, if this frame carries one.

    Per the MCP spec's Error Handling section, protocol errors (unknown tool,
    invalid arguments, server errors) come back as a top-level `error` object
    INSTEAD of `result`:
        {"jsonrpc": "2.0", "id": 3,
         "error": {"code": -32602, "message": "Unknown tool: invalid_tool_name"}}

    Previously call_tool read only `data.get("result", {})`, so an error frame
    produced an empty dict, then a generic "Empty response". The actual message
    -- the single most useful line for diagnosing a bad tool call -- was thrown
    away. Surface it instead.
    """
    err = data.get("error")
    if not isinstance(err, dict):
        return None
    code = err.get("code")
    message = err.get("message") or "unknown JSON-RPC error"
    return f"MCP_PROTOCOL_ERROR: {message}" + (f" (code {code})" if code is not None else "")


def _parse_response(body: str) -> Tuple[Optional[Any], Optional[str], bool]:
    """Parse an SSE or direct-JSON MCP response.

    Returns (content, protocol_error, is_tool_error):
      content        -- real tool output, or None if none was found
      protocol_error -- JSON-RPC error message, or None
      is_tool_error  -- True when the result carried MCP's `isError: true`

    WHY the two extra return values (2026-08-27): the MCP spec defines TWO
    distinct failure channels and this function previously honored NEITHER --
    it returned bare content and dropped the rest of the envelope.

      1. Protocol errors  -> top-level `error` (see _rpc_error_message).
      2. Tool execution errors -> `result.isError: true`, with the error text
         delivered in `content` exactly where real data normally sits:
            {"result": {"content": [{"type": "text",
                                     "text": "Failed to fetch: rate limit"}],
                        "isError": true}}

    Channel 2 is the dangerous one, and it is the same failure SHAPE as the
    Model Armor incident (run_20260826_211251_rlwe): HTTP 200, non-empty body,
    but the body is an error message rather than the data that was requested.
    Without reading `isError`, call_tool returned ok=True and that error string
    became an evidence item the LLM then reasoned over as if it were cluster
    state. Only the narrow _is_not_found_result() substring check caught any of
    these; every other tool error -- permission denied, invalid resourceType,
    upstream rate limit -- passed straight through as a success.

    The spec is explicit that clients must do this: "Clients SHOULD ... Validate
    tool results before passing to LLM."
    https://modelcontextprotocol.io/specification/2025-06-18/server/tools
    """
    first_protocol_error: Optional[str] = None

    # Try SSE first — scan all data: frames, skip empty/notification ones
    for line in body.splitlines():
        if line.startswith("data:"):
            try:
                data = json.loads(line[5:].strip())
            except Exception:
                continue
            if not isinstance(data, dict):
                continue

            # Keep the FIRST protocol error seen, but keep scanning: a later
            # frame may still carry real content, and real content wins.
            if first_protocol_error is None:
                first_protocol_error = _rpc_error_message(data)

            result = data.get("result", {})
            if not isinstance(result, dict):
                continue
            content = _extract_content(result)
            if content is not None:
                return content, None, bool(result.get("isError"))

    if first_protocol_error is not None:
        return None, first_protocol_error, False

    # Try direct JSON
    try:
        data = json.loads(body)
    except Exception:
        return None, None, False
    if not isinstance(data, dict):
        return None, None, False

    protocol_error = _rpc_error_message(data)
    if protocol_error is not None:
        return None, protocol_error, False

    result = data.get("result", {})
    if not isinstance(result, dict):
        return None, None, False
    return _extract_content(result), None, bool(result.get("isError"))


def get_source_descriptions() -> str:
    """Returns MCP source names + one-line descriptions only — for Phase 1 routing."""
    return "\n".join(
        f"  {name}: {cfg['description']} [{cfg.get('ga_status', 'GA')}]"
        for name, cfg in MCP_REGISTRY.items()
    )


def get_tools_for_source(mcp_source: str) -> str:
    """Returns tool descriptions for ONE MCP source only — for Phase 2 routing."""
    if mcp_source == "gke_remote_mcp":
        return (
            "Tools (provide: namespace, name, resourceType — NOT parent):\n"
            "  list_k8s_events(namespace, name, resourceType='pod')\n"
            "  describe_k8s_resource(resourceType, name, namespace)\n"
            "  get_k8s_resource(resourceType, name, namespace)\n"
            "  get_k8s_logs(namespace, name, container=<see SIDECAR rule below>, previous=false, tail='100')\n"
            "  list_k8s_api_resources()   ← no args\n"
            "  get_k8s_cluster_info()     ← no args"
        )
    return (
        "Tools (all read-only):\n"
        "\n"
        "  Pod:\n"
        "    list_pods(namespace)\n"
        "    describe_pod_detail(namespace, pod_name)\n"
        "    get_current_logs(namespace, pod_name, container=<see SIDECAR rule below>)\n"
        "    get_previous_logs(namespace, pod_name, container=<see SIDECAR rule below>)  ← use when pod crashed\n"
        "    list_events(namespace, pod_name)\n"
        "\n"
        "  Deployment / ReplicaSet:\n"
        "    list_deployments(namespace)\n"
        "    describe_deployment(namespace, deployment_name)\n"
        "    list_replicasets(namespace)\n"
        "    describe_replicaset(namespace, replicaset_name)\n"
        "\n"
        "  StatefulSet / DaemonSet:\n"
        "    list_statefulsets(namespace)\n"
        "    describe_statefulset(namespace, name)\n"
        "    list_daemonsets(namespace)\n"
        "    describe_daemonset(namespace, name)\n"
        "\n"
        "  Service / Connectivity:\n"
        "    list_services(namespace)\n"
        "    describe_service(namespace, service_name)\n"
        "    list_endpoints(namespace)\n"
        "\n"
        "  Node / Scheduling:\n"
        "    list_nodes()                                  ← no namespace needed\n"
        "    describe_node(node_name)\n"
        "\n"
        "  ConfigMap:\n"
        "    list_configmaps(namespace)\n"
        "    get_configmap(namespace, name)\n"
        "\n"
        "  HPA / Scaling:\n"
        "    list_hpas(namespace)\n"
        "    describe_hpa(namespace, name)\n"
        "\n"
        "  Storage:\n"
        "    list_pvcs(namespace)\n"
        "    describe_pvc(namespace, name)\n"
        "\n"
        "  Namespace events (broader view):\n"
        "    list_namespace_events(namespace)              ← all events, no pod filter\n"
        "\n"
        "  Jobs:\n"
        "    list_jobs(namespace)\n"
        "    describe_job(namespace, job_name)"
    )


def get_registry_prompt() -> str:
    """Returns MCP registry description for mcp_router prompt."""
    lines = [
        "Available MCP sources (call ONE at a time):",
        "Preferred: gke_remote_mcp (Google-managed, try first)",
        "Fallback:  k8s_mcp (custom Cloud Run, used if gke_remote fails)",
        "",
        "gke_remote_mcp tools — LLM provides: namespace, name(pod), resourceType",
        "  (parent field is built automatically — do NOT include it in arguments)",
        "  list_k8s_events(namespace, name, resourceType='pod')",
        "  describe_k8s_resource(resourceType='pod', name, namespace)",
        "  get_k8s_resource(resourceType='pod', name, namespace)",
        "  get_k8s_logs(namespace, podName, previous=false, tailLines=100)",
        "  list_k8s_api_resources()   ← no args needed",
        "  get_k8s_cluster_info()     ← no args needed",
        "",
        "k8s_mcp tools (fallback — use if gke_remote_mcp fails):",
        "  list_pods(namespace)",
        "  describe_pod_detail(namespace, pod_name)",
        "  get_current_logs(namespace, pod_name)",
        "  get_previous_logs(namespace, pod_name)",
        "  list_events(namespace, pod_name)",
        "  list_deployments(namespace)",
    ]
    return "\n".join(lines)


def resolve_cluster(cluster_name: str) -> Dict[str, Any]:
    """Resolve cluster info from registry. Raises if cluster is unknown."""
    registry = _get_cluster_registry()
    if cluster_name in registry:
        return {**registry[cluster_name], "cluster_name": cluster_name}
    known = list(registry.keys())
    raise ValueError(
        f"Cluster '{cluster_name}' not in registry. Known clusters: {known}. "
        "To add a cluster: set var.additional_clusters in Terraform "
        "(iac/agent/variables.tf) and apply — do not hand-edit clusters.json "
        "in GCS, it will be overwritten on the next apply."
    )


def _alias_index(registry: Dict[str, Any]) -> Dict[str, str]:
    """Map lowercased approved alias -> canonical cluster id, enabled clusters only."""
    index: Dict[str, str] = {}
    for canonical_id, info in registry.items():
        if not info.get("enabled", True):
            continue
        for alias in info.get("aliases", []):
            index[alias.lower()] = canonical_id
    return index


def resolve_cluster_routing(
    cluster_hint: str = "",
    cluster_guess: str = "",
    namespace_hint: str = "",
    project_hint: str = "",
    environment_hint: str = "",
) -> Dict[str, Any]:
    """
    Deterministic cluster routing. NEVER guesses — every branch either resolves to a
    specific, registry-known, enabled cluster with a stated reason, or returns
    resolved=False so the caller safe-stops instead of picking one.

    Priority order:
      1. exact_id                — cluster_hint (verified: from the caller/alert system,
                                    not free-text LLM extraction) matches a canonical
                                    cluster id exactly.
      2. verified_alert_metadata — cluster_hint matches a canonical id case-insensitively.
      3. approved_alias          — cluster_hint (or, only when no hint was supplied at all,
                                    the unverified LLM-extracted cluster_guess) matches a
                                    registered alias.
      4. project_env_namespace   — project/environment/namespace hints uniquely identify
                                    exactly one enabled cluster. Ambiguous (>1 match) is
                                    treated the same as no match — never guessed.
      5. unresolved              — none of the above. Caller must safe-stop.

    A candidate that matches by id/alias but belongs to a disabled cluster is treated as
    unresolved, not silently routed.
    """
    registry = _get_cluster_registry()

    if not registry:
        return {
            "resolved": False,
            "cluster_name": "",
            "method": "unresolved",
            "reason": "Cluster registry is empty or unavailable — cannot route to any cluster.",
        }

    def _enabled(canonical_id: str) -> bool:
        return registry.get(canonical_id, {}).get("enabled", True)

    # Tier 1 — exact id, case-sensitive, verified hint only.
    if cluster_hint and cluster_hint in registry:
        if _enabled(cluster_hint):
            return {
                "resolved": True, "cluster_name": cluster_hint, "method": "exact_id",
                "reason": f"'{cluster_hint}' matched a registered cluster id exactly.",
            }
        return {
            "resolved": False, "cluster_name": "", "method": "unresolved",
            "reason": f"Cluster '{cluster_hint}' is registered but disabled.",
        }

    # Tier 2 — verified alert metadata: same hint, case-insensitive id match.
    if cluster_hint:
        for canonical_id in registry:
            if canonical_id.lower() == cluster_hint.lower():
                if _enabled(canonical_id):
                    return {
                        "resolved": True, "cluster_name": canonical_id,
                        "method": "verified_alert_metadata",
                        "reason": (
                            f"'{cluster_hint}' matched cluster id '{canonical_id}' "
                            "case-insensitively."
                        ),
                    }
                return {
                    "resolved": False, "cluster_name": "", "method": "unresolved",
                    "reason": f"Cluster '{canonical_id}' is registered but disabled.",
                }

    # Tier 3 — approved alias. Prefer the verified hint; only fall back to the
    # unverified LLM guess when no hint was supplied at all.
    alias_index = _alias_index(registry)
    alias_candidate = cluster_hint or cluster_guess
    alias_verified = bool(cluster_hint)
    if alias_candidate:
        canonical_id = alias_index.get(alias_candidate.lower())
        if canonical_id:
            return {
                "resolved": True, "cluster_name": canonical_id, "method": "approved_alias",
                "reason": (
                    f"'{alias_candidate}' matched approved alias for cluster '{canonical_id}' "
                    f"({'verified hint' if alias_verified else 'unverified LLM guess'})."
                ),
            }

    # A verified, explicit cluster_hint that didn't match tiers 1-3 is an UNKNOWN cluster,
    # not a missing one. Stop here — never fall through to tier 4's namespace-based
    # resolution using unrelated hints, which could silently route to a different,
    # unrelated cluster than the one actually requested.
    if cluster_hint:
        return {
            "resolved": False, "cluster_name": "", "method": "unresolved",
            "reason": (
                f"Cluster '{cluster_hint}' was explicitly requested but is not a registered "
                "cluster id or alias — refusing to fall back to namespace-based routing."
            ),
        }

    # Tier 4 — project / environment / namespace uniqueness.
    if namespace_hint or project_hint or environment_hint:
        candidates = []
        for canonical_id, info in registry.items():
            if not info.get("enabled", True):
                continue
            if project_hint and info.get("project") != project_hint:
                continue
            if environment_hint and info.get("environment") != environment_hint:
                continue
            if namespace_hint:
                allowed = info.get("allowed_namespaces") or []
                if not allowed or namespace_hint not in allowed:
                    continue
            candidates.append(canonical_id)
        if len(candidates) == 1:
            return {
                "resolved": True, "cluster_name": candidates[0], "method": "project_env_namespace",
                "reason": (
                    f"Uniquely resolved via project='{project_hint}' "
                    f"environment='{environment_hint}' namespace='{namespace_hint}'."
                ),
            }
        if len(candidates) > 1:
            return {
                "resolved": False, "cluster_name": "", "method": "unresolved",
                "reason": (
                    f"Ambiguous: {len(candidates)} clusters match project/environment/namespace "
                    f"hints ({candidates}) — refusing to guess."
                ),
            }

    # Tier 5 — human safe-stop.
    return {
        "resolved": False,
        "cluster_name": "",
        "method": "unresolved",
        "reason": (
            "No cluster was explicitly provided and none could be deterministically resolved "
            "via exact id, alias, or project/environment/namespace — stopping instead of "
            "guessing."
        ),
    }
