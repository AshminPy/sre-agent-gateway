# Cluster Routing

> **Implementation Status:** IMPLEMENTED
> **Last Verified:** 2026-08-08 — `agent/mcp_client.py:645-775`
> **Source of Truth:** `agent/mcp_client.py:645-775` (`resolve_cluster_routing()`)
> **Owner:** SRE Agent platform team.

This page is about **which cluster** an incident is about. It's a separate question from **which MCP source** to use once the cluster is known — see [Dynamic MCP Routing](dynamic-mcp-routing.md) for that.

## How the agent determines the correct cluster — the 5-tier priority chain

`resolve_cluster_routing()` (`agent/mcp_client.py:645-775`) runs these tiers **in order**, stopping at the first one that resolves to exactly one cluster:

| Tier | Name (verbatim from code) | Check |
|---|---|---|
| 1 | `exact_id` | A verified hint (`cluster_hint`, from the caller's own structured fields — never LLM-guessed) exactly matches a canonical cluster ID in the registry (case-sensitive) |
| 2 | `verified_alert_metadata` | Same verified hint, but a case-insensitive match |
| 3 | `approved_alias` | The hint (or, **only if no hint was supplied at all**, the LLM's free-text `cluster_guess`) matches a registered alias in the registry |
| 4 | `project_env_namespace` | Filtering all enabled registry clusters by project/environment/namespace hints resolves to **exactly one** candidate — two or more matching candidates is treated as ambiguous, and the agent refuses to guess |
| 5 | `unresolved` | None of the above resolved anything — this is the human safe-stop |

An empty cluster registry short-circuits straight to `unresolved` before any tier even runs.

**The critical design point**: Tier 3 only ever falls back to the model's free-text guess when there is no verified hint at all, and even then, that guess must match a pre-approved alias — the model cannot cause the agent to route to an arbitrary, unregistered cluster name. Tier 4's "exactly one candidate" rule means an ambiguous match (two clusters both plausible) is treated the same as no match — the system never picks arbitrarily between two candidates.

## Where cluster/project/environment mappings are stored

A JSON registry file, `clusters.json`, stored in a dedicated GCS bucket, read by `_get_cluster_registry()` with a 5-minute TTL cache (`agent/mcp_client.py:205-217`). Each entry has: `name` (canonical ID), `aliases`, `project`, `region`, `type` (`gke`/`custom`), `environment`, `allowed_namespaces`, `owner`, `enabled`, and `mcp_url` (falls back to an env var if absent).

**⚠️ Known operational limitation — `clusters.json` is wiped on every `terraform apply`.** The Terraform template that generates this file (`iac/agent/clusters.json.tftpl`) hardcodes exactly **one** cluster object — there is no loop/list construct, and the `google_storage_bucket_object` resource that uploads it has no `lifecycle { ignore_changes }` block. This means **any manually-added second cluster entry is destroyed on the next `terraform apply`.** See [Adding a New GKE Cluster](../runbooks/add-gke-cluster.md) for the current workaround and what needs to change to fix this properly.

## What data comes from PagerDuty

**Nothing today — PLANNED, not implemented.** No PagerDuty integration exists in this codebase.

## What data comes from user requests

The payload's explicit `cluster`/`namespace`/`pod` fields, plus a `resource_hints` sub-object that `input_normalizer` uses to build the verified `cluster_hint`/`project_hint`/`environment_hint` fields the routing chain above consumes.

## How cluster identity is verified

By requiring a match against the registry — the routing chain never invents a cluster name. "Verified" in the tier names above specifically means "came from the caller's structured fields, not LLM free-text extraction."

## How we prevent the agent from investigating the wrong cluster

The whole point of the 5-tier chain with a hard safe-stop at the end: if the chain can't confidently resolve to exactly one cluster, the agent stops rather than guessing. This is enforced twice — once in `context_resolver` (the primary gate) and again, independently, in `mcp_router` (defense-in-depth, in case the registry went stale between the two nodes given the 5-minute TTL cache).

## What happens if two clusters contain the same namespace/pod name?

Tier 4's namespace-based matching only fires when it narrows to exactly one candidate cluster — if the same namespace exists (as an allowed namespace) on two different enabled clusters and no more specific hint (project/environment) disambiguates, the chain treats this as unresolved rather than picking one.

## What happens when cluster information is missing entirely?

Falls through all 5 tiers to `unresolved` — safe-stop, `investigation.status="failed"`, `loop_exit_reason="cluster_unresolved"`, straight to `rca_builder` with an explanatory error. No default cluster is ever substituted.

## What happens if the cluster is unreachable?

This is a tool-call-level failure, not a routing failure — the cluster *identity* resolves fine, but the subsequent MCP tool call to it fails and is recorded/logged as a tool failure (see [Investigation Loop](investigation-loop.md#failed-mcp-call-behavior)).

## What happens if the agent has no IAM permissions for that cluster

Same as above — the tool call itself fails (likely a `403`/`Forbidden` from the underlying API), recorded as a tool failure, not a routing failure. The routing chain has no visibility into IAM permission state; it only checks the cluster registry.

---

**Related pages:** [Dynamic MCP Routing](dynamic-mcp-routing.md) · [GKE vs Non-GKE Kubernetes Access](gke-vs-nongke.md) · [Adding a New GKE Cluster](../runbooks/add-gke-cluster.md)
