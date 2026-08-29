"""Evidence domain classification — deterministic, tool-name-based.

Two evidence items from the same domain (e.g. ten log lines) are ONE independent source for
corroboration purposes, not N. Classification is by tool name, not content inspection: cheap,
reliable, and extensible — a new MCP tool needs one new table entry, never a scorer change.
"""
from __future__ import annotations

from enum import Enum

from agent.mcp_client import _map_to_custom_tool


class EvidenceDomain(str, Enum):
    KUBERNETES_STATUS = "kubernetes_status"
    KUBERNETES_EVENTS = "kubernetes_events"
    CURRENT_LOGS = "current_logs"
    PREVIOUS_LOGS = "previous_logs"
    WORKLOAD_CONFIG = "workload_config"
    RESOURCE_LIMITS = "resource_limits"
    CLOUD_LOGGING = "cloud_logging"
    METRICS = "metrics"
    CHANGE_HISTORY = "change_history"
    ALERT_METADATA = "alert_metadata"
    RUNBOOK_HISTORY = "runbook_history"
    UNKNOWN = "unknown"


# Canonical (custom-K8s-MCP-named) tool → domain. GKE Remote MCP tool names are normalized to
# this set via agent.mcp_client._map_to_custom_tool before lookup, so both MCP sources share
# one table — no per-source duplication.
_TOOL_DOMAIN = {
    "list_pods": EvidenceDomain.KUBERNETES_STATUS,
    "describe_pod_detail": EvidenceDomain.KUBERNETES_STATUS,
    "get_current_logs": EvidenceDomain.CURRENT_LOGS,
    "get_previous_logs": EvidenceDomain.PREVIOUS_LOGS,
    "list_events": EvidenceDomain.KUBERNETES_EVENTS,
    "list_deployments": EvidenceDomain.WORKLOAD_CONFIG,
}

# Domains no current MCP tool can supply. Never counted as "required now" — see policy.py's
# EvidenceRequirement.future_source. Kept here so classify_tool() has a documented answer for
# anything that's added later without a domain still being ambiguous.
FUTURE_SOURCE_DOMAINS = frozenset({
    EvidenceDomain.CLOUD_LOGGING,
    EvidenceDomain.METRICS,
    EvidenceDomain.CHANGE_HISTORY,
    EvidenceDomain.ALERT_METADATA,
    EvidenceDomain.RUNBOOK_HISTORY,
})

# Domains treated as corroborating the same underlying signal, not independent — weight applied
# in scorer.py's independent_corroboration component, not here (this module only classifies).
RELATED_DOMAIN_GROUPS = (
    frozenset({EvidenceDomain.CURRENT_LOGS, EvidenceDomain.PREVIOUS_LOGS}),
)

# resourceType -> domain, for GKE Remote MCP's describe_k8s_resource/get_k8s_resource (both
# normalize to the custom MCP's describe_pod_detail regardless of resourceType today -- see
# docs/management/confidence-genericity-review-2026-08-28.md #15.6). Only resource types with an
# unambiguous existing domain are listed; service/node/configmap/job are deliberately omitted --
# no existing EvidenceDomain fits them without guessing, same open item as _TOOL_DOMAIN's
# 21/27-UNKNOWN gap (report #2 row 1). Omitted types keep today's behavior (fall through to the
# tool-name-only classification below).
_RESOURCE_TYPE_DOMAIN = {
    "pod": EvidenceDomain.KUBERNETES_STATUS,
    "deployment": EvidenceDomain.WORKLOAD_CONFIG,
    "replicaset": EvidenceDomain.WORKLOAD_CONFIG,
    "statefulset": EvidenceDomain.WORKLOAD_CONFIG,
    "daemonset": EvidenceDomain.WORKLOAD_CONFIG,
}

# Tool name -> (arg name to inspect, {arg value: domain}). Only tools whose real semantic
# meaning changes with a call argument need an entry here -- everything else is classified by
# name alone, unchanged. Verified against real GKE Remote MCP schema + live production tool-call
# data (report #15.6): get_k8s_logs's "previous" arg is a real bool, confirmed live
# (args={'previous': True} in a real oomkilled-001 run); describe/get_k8s_resource's
# "resourceType" is a real string arg, confirmed live (deployment/replicaset/service/configmap/
# job all seen in production tool calls).
_ARGS_DOMAIN_OVERRIDES = {
    "get_k8s_logs": ("previous", {True: EvidenceDomain.PREVIOUS_LOGS}),
    "describe_k8s_resource": ("resourceType", _RESOURCE_TYPE_DOMAIN),
    "get_k8s_resource": ("resourceType", _RESOURCE_TYPE_DOMAIN),
}


def classify_tool(tool_name: str, args: dict | None = None) -> EvidenceDomain:
    """Classify a tool name (either MCP source) into an evidence domain.

    `args` is optional (defaults to None/{}) so every existing no-args call site keeps working
    unchanged. When the tool is one of _ARGS_DOMAIN_OVERRIDES's entries, the named arg's value
    can shift the domain -- e.g. get_k8s_logs(previous=True) -> PREVIOUS_LOGS instead of the
    tool-name-only CURRENT_LOGS every get_k8s_logs call used to collapse to (confirmed live bug,
    docs/management/confidence-genericity-review-2026-08-28.md #15.6). Falls back to the
    tool-name-only table below for every other tool, and for override tools whose arg value has
    no listed mapping (e.g. previous=False, or an omitted resourceType).

    Unmapped tools return UNKNOWN — logged as a gap by the scorer, never silently dropped and
    never silently counted as a fresh independent domain.
    """
    if not tool_name:
        return EvidenceDomain.UNKNOWN
    if args and tool_name in _ARGS_DOMAIN_OVERRIDES:
        arg_name, value_map = _ARGS_DOMAIN_OVERRIDES[tool_name]
        override = value_map.get(args.get(arg_name))
        if override is not None:
            return override
    canonical = tool_name if tool_name in _TOOL_DOMAIN else _map_to_custom_tool(tool_name)
    return _TOOL_DOMAIN.get(canonical or "", EvidenceDomain.UNKNOWN)


def domain_weight(domain: EvidenceDomain, present_domains: set) -> float:
    """Weight this domain contributes to independent corroboration, given what else is present.

    Related domains present together (e.g. current + previous logs) each count at 0.5 instead
    of 1.0, so two related domains together contribute 1.0 total — one independent source, not
    two — per "current logs and previous container logs may be related evidence, not fully
    independent."

    2026-08-27: UNKNOWN now contributes 0.0. classify_tool's own docstring
    already promised it was "never silently counted as a fresh independent
    domain", but this function returned 1.0 for it -- so evidence from a tool the
    agent cannot even classify bought a full independent corroborating source,
    worth 0.5 of the independent_corroboration component on its own. UNKNOWN
    means "we do not know what this evidence is"; that cannot corroborate
    anything. Same class as _ground_claim's empty/empty branch: code
    contradicting its own stated contract in the generous direction.
    """
    if domain is EvidenceDomain.UNKNOWN:
        return 0.0
    for group in RELATED_DOMAIN_GROUPS:
        if domain in group and len(group & present_domains) > 1:
            return 0.5
    return 1.0
