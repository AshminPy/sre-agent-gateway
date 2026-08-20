# ADR-012: GKE Remote MCP for GKE clusters, a separate custom MCP for non-GKE/on-prem

Status: Accepted design; GKE path Accepted and live-verified; custom MCP path code-complete but NOT deployed today — see Tradeoffs

> **Re-verified 2026-08-20:** still accurate. `enable_custom_mcp` still defaults to `false`
> (`iac/agent/variables.tf`), and issue #85 ("Custom Cloud Run MCP fallback is
> non-operational as deployed") remains **open** — no Load Balancer or Serverless NEG
> exists in `iac/`, so the service has no network path even when enabled. No change needed
> to this decision.

## Context

The agent needs to reach two structurally different kinds of clusters: GKE
clusters, where Google offers a managed, purpose-built MCP server
(`gke_remote_mcp`), and non-GKE/on-prem clusters, which that managed service
cannot reach at all — it's GKE-specific. A single MCP source can't cover
both, since one of them (GKE Remote MCP) is a fixed Google endpoint with no
concept of an arbitrary external cluster, and the other requires our own
network path and auth into wherever that cluster actually lives.

## Decision

Register two distinct MCP sources in `MCP_REGISTRY`
(`agent/mcp_client.py:98-113`), and route between them by cluster type,
determined once cluster identity is resolved (`mcp_router.py:133-136`):
`cluster_type == "gke"` → `gke_remote_mcp` first; anything else → `k8s_mcp`
(the custom Cloud Run MCP) first.

- **`gke_remote_mcp`** — Google-managed, `container.googleapis.com/mcp/read-only`,
  6 tools, cross-project IAM only (`roles/container.viewer`,
  `roles/mcp.toolUser`, `roles/logging.viewer`, `roles/monitoring.viewer`) —
  no Kubernetes RBAC configuration needed, since GKE Remote MCP handles that
  internally. This is the live, tested path for GKE clusters.
- **`k8s_mcp`** — our own Cloud Run service (`mcp/server.py`, FastMCP-based,
  27 read-only tools wrapped by a `@guarded()` security decorator), intended
  as both the path for non-GKE/on-prem clusters (which it reaches via a
  Connect Gateway kubeconfig context or a direct endpoint) and as a fallback
  when GKE Remote MCP fails (`_map_to_custom_tool()`,
  `agent/mcp_client.py:324-334,443-457`).

## Alternatives Considered

- **Build one custom MCP server for every cluster, GKE included, skip
  Google's managed service entirely** — rejected because it would mean
  operating and securing a GKE-reaching server ourselves for the common
  case, duplicating a service Google already runs, patches, and scales for
  free.
- **Only support GKE, treat non-GKE/on-prem as explicitly out of scope** —
  rejected as a permanent stance; a real, documented need exists for on-prem
  investigation (the `onprem-001` golden case exists specifically to exercise
  this path), even though — as the Tradeoffs below make plain — that need is
  not actually met by the live deployment today.
- **Route by trying GKE Remote MCP for everything and only falling back to
  custom MCP on failure, with no explicit cluster-type check** — rejected in
  favor of the current type-based routing, because a non-GKE cluster would
  never succeed against `gke_remote_mcp` in the first place (it's not a GKE
  endpoint at all) — trying it first would just be a guaranteed-failure
  round trip before falling back, adding latency with no chance of success.

## Reason

Splitting by cluster type at the routing layer, rather than trying to force
one MCP source to handle both cases, matches the real infrastructure
constraint: GKE Remote MCP is fundamentally GKE-only, and no amount of
configuration changes that. Keeping `k8s_mcp` as our own service is also
what makes non-GKE/on-prem support possible at all — there's no managed
Google service to lean on for that case. See [GKE vs Non-GKE
Access](architecture/gke-vs-nongke.md) and [MCP
Architecture](architecture/mcp-architecture.md).

## Tradeoffs

**Be precise about the honest, current split — this is "built, not
deployed," not "doesn't exist":**

- The GKE path is real, live, and load-bearing today — this half of the
  decision is fully realized in production.
- The custom MCP server's *code* is complete and proven working when run and
  tested manually: 18 of 19 real tool calls succeeded against a `kind`
  cluster (`sre-lab`) through a manually-configured Connect Gateway
  registration; the one expected failure (`list_nodes`, forbidden) was
  surfaced as a clean structured error, not a crash — proof the `@guarded()`
  error handling works as designed.
- But the *deployed* Cloud Run service cannot reach any cluster today, for
  three independent reasons: (1) `enable_custom_mcp` defaults `false` and is
  not overridden in the live `iac/agent/terraform.tfvars` — the service
  isn't even deployed; (2) even if deployed,
  `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` requires an Internal Load
  Balancer + Serverless NEG that doesn't exist anywhere in this repo's
  Terraform; (3) the deployed container's own env vars set only
  `PROJECT_ID` — none of the connectivity variables `mcp/server.py` needs
  (`GKE_CLUSTER_ENDPOINT`, `K8S_MCP_KUBE_CONTEXT`) are set, so even a
  reachable container would fall through to a "load local kubeconfig" branch
  with no kubeconfig file inside a Cloud Run container.
- Practical consequence: the GKE-failure fallback to `k8s_mcp` currently
  degrades to a tool failure rather than a working handoff, and the on-prem
  path is unproven in production — a real gap, not a documentation gap. The
  Connect Gateway infrastructure behind it is also not Terraform-managed
  today (manual `gcloud` setup only), and successful reads through it aren't
  currently audit-logged (`DATA_READ` audit logging for
  `connectgateway.googleapis.com` is off).
- What's needed to close this gap is enumerated concretely in [GKE vs
  Non-GKE Access](architecture/gke-vs-nongke.md#whats-needed-to-actually-wire-this-together)
  — building the Load Balancer/NEG, setting the connectivity env vars,
  granting `roles/gkehub.gatewayReader`, converting the manual Fleet/RBAC
  steps to Terraform, and extending `clusters.json`'s schema to record which
  reachability mode a cluster needs.

## Related ADRs

- [ADR-004: MCP as the tool-access protocol](ADR-004-mcp-tool-access-protocol.md)
- [ADR-011: Terraform-managed cluster registry](ADR-011-terraform-managed-cluster-registry.md)
- [ADR-005: Read-only by design](ADR-005-read-only-by-design.md)
