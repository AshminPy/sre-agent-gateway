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
import json
import logging
import os
import time
from typing import Any, Dict, Optional

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

ALLOWED_TOOLS = GKE_REMOTE_TOOLS | CUSTOM_K8S_TOOLS

# Write-style actions — always blocked
BLOCKED_ACTIONS = frozenset({
    "delete", "create", "patch", "update", "apply",
    "exec", "port-forward", "scale", "rollout",
})

# ── MCP Source Registry ───────────────────────────────────────────
MCP_REGISTRY = {
    "gke_remote_mcp": {
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
    """Map GKE Remote MCP tool → equivalent custom K8s MCP tool."""
    mapping = {
        "list_k8s_events":       "list_events",
        "describe_k8s_resource": "describe_pod_detail",
        "get_k8s_resource":      "describe_pod_detail",
        "get_k8s_logs":          "get_current_logs",
        "list_k8s_api_resources": "list_pods",
        "get_k8s_cluster_info":  "list_pods",
    }
    return mapping.get(gke_tool)


def call_tool(
    mcp_source: str,
    tool_name: str,
    arguments: Dict[str, Any],
    run_id: str = "",
    cluster_name: str = "",
) -> Dict[str, Any]:
    """
    Call one tool on one MCP source.
    Validates allowlist before any network call.
    Builds correct args for GKE Remote MCP (parent field required).
    Auto-falls-back to custom K8s MCP if GKE Remote fails.
    """
    validation_error = _validate_tool(tool_name, arguments)
    if validation_error:
        log.warning("call_tool BLOCKED: %s", validation_error)
        return {
            "ok": False, "error": f"BLOCKED: {validation_error}",
            "tool": tool_name, "mcp_source": mcp_source,
            "duration_s": 0, "blocked": True,
        }

    cluster_info  = _get_cluster_registry().get(cluster_name, {})
    source_config = MCP_REGISTRY.get(mcp_source, {})
    is_gke_remote = (mcp_source == "gke_remote_mcp")

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

            # Auto-fallback to custom MCP on GKE Remote errors
            if is_gke_remote:
                fallback      = cluster_info.get("mcp_fallback", "k8s_mcp")
                fallback_tool = _map_to_custom_tool(tool_name)
                if fallback and fallback_tool and fallback_tool in CUSTOM_K8S_TOOLS:
                    log.info(
                        "call_tool: GKE Remote failed → fallback %s.%s",
                        fallback, fallback_tool,
                    )
                    # Pass original namespace/pod as custom MCP args
                    fallback_args = {
                        "namespace": namespace,
                        "pod_name":  pod_name,
                    }
                    return call_tool(fallback, fallback_tool, fallback_args, run_id, cluster_name)

            return {
                "ok": False, "error": error_msg,
                "tool": tool_name, "mcp_source": mcp_source, "duration_s": duration,
            }

        # Parse SSE or direct JSON response
        content = _parse_response(resp.text)
        if content is not None:
            return {
                "ok": True, "result": content,
                "tool": tool_name, "mcp_source": mcp_source, "duration_s": duration,
            }

        return {
            "ok": False, "error": "Empty response",
            "tool": tool_name, "mcp_source": mcp_source, "duration_s": duration,
        }

    except Exception as e:
        return {
            "ok": False, "error": str(e),
            "tool": tool_name, "mcp_source": mcp_source,
            "duration_s": round(time.time() - start, 2),
        }


def _parse_response(body: str) -> Optional[Any]:
    """Parse SSE or direct JSON response from MCP server."""
    # Try SSE first
    for line in body.splitlines():
        if line.startswith("data:"):
            try:
                data    = json.loads(line[5:].strip())
                result  = data.get("result", {})
                content = result.get("structuredContent") or result.get("content", [])
                if isinstance(content, list) and content:
                    first = content[0]
                    if isinstance(first, dict) and first.get("type") == "text":
                        try:
                            return json.loads(first["text"])
                        except Exception:
                            return first["text"]
                return content or result
            except Exception:
                pass

    # Try direct JSON
    try:
        data    = json.loads(body)
        result  = data.get("result", {})
        content = result.get("structuredContent") or result.get("content", [])
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text":
                try:
                    return json.loads(first["text"])
                except Exception:
                    return first["text"]
        return content or result or None
    except Exception:
        pass

    return None


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
            "  get_k8s_logs(namespace, name, previous=false, tail='100')\n"
            "  list_k8s_api_resources()   ← no args\n"
            "  get_k8s_cluster_info()     ← no args"
        )
    return (
        "Tools (all read-only):\n"
        "\n"
        "  Pod:\n"
        "    list_pods(namespace)\n"
        "    describe_pod_detail(namespace, pod_name)\n"
        "    get_current_logs(namespace, pod_name)\n"
        "    get_previous_logs(namespace, pod_name)       ← use when pod crashed\n"
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
