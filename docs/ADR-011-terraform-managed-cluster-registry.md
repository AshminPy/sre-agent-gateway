# ADR-011: The cluster registry is Terraform-managed and dynamic, not hand-edited

Status: Accepted (fixed 2026-08-09, PR #52)

## Context

`resolve_cluster_routing()` (`agent/mcp_client.py:648-777`) needs a source of
truth mapping cluster IDs/aliases to project, region, type, and allowed
namespaces before it can safely route an investigation — this is the
registry the whole 5-tier routing chain (see [Cluster
Routing](architecture/cluster-routing.md)) checks against, and an unresolved
or wrong mapping means either a safe-stop or, worse, investigating the wrong
cluster. Before 2026-08-09, `clusters.json` (the registry file, served from
GCS) supported effectively one cluster at a time: `iac/agent/main.tf`
rendered it from only `var.gke_cluster_name`, so adding a second cluster
meant either hand-editing the file directly in the bucket (which the next
`terraform apply` would silently overwrite, since Terraform still owned that
object) or accepting that multi-cluster wasn't really supported. This was a
real, previously-documented operational limitation across several runbooks.

## Decision

Make the registry fully Terraform-managed and support any number of
clusters, driven by a typed input variable:

- `var.additional_clusters` (`iac/agent/variables.tf:41`) — a
  `map(object(...))` where each entry supplies the same fields the registry
  schema needs (aliases, project, region, type, environment, allowed
  namespaces, owner, enabled — 8 fields total, `iac/agent/variables.tf`, no `mcp_url` field
  exists in this typed schema — Terraform's `object({...})` type constraint would reject an
  unrecognized key, so a per-cluster `mcp_url` cannot be set via `var.additional_clusters`
  today; the runtime registry code (`agent/mcp_client.py:189`) still reads an `mcp_url` key
  if present, falling back to the `K8S_MCP_URL` env var — that fallback is the only reachable
  path for clusters added through this Terraform mechanism).
- `iac/agent/main.tf` renders `clusters.json` via `jsonencode(...)`
  (`main.tf:54`), merging `var.additional_clusters` with the always-present
  default cluster (`var.gke_cluster_name`) into one map, and writes the
  result to the GCS object every apply.
- A `validation` block on `additional_clusters` (whitespace-trimmed key
  comparison) makes a name collision between the default cluster and an
  additional-cluster key **fail the plan**, rather than silently overriding
  the default entry.
- Terraform remains the sole source of truth for the file: hand-editing
  `clusters.json` directly in GCS is still not supported and is still
  overwritten on the next apply — that has not changed. What changed is that
  the *intended* way to add a cluster (`var.additional_clusters`) now
  actually works and survives repeated applies, instead of only ever
  supporting one cluster total.
- Verified live: `terraform test` (4/4 passing) and
  `pytest tests/test_multi_cluster_registry.py tests/test_mcp_router.py`
  (13/13 passing) both ran against the real change, not just reviewed
  statically.

## Alternatives Considered

- **Allow direct, out-of-band edits to `clusters.json` in GCS** (skip
  Terraform ownership entirely for this file) — rejected because it would
  create exactly the drift risk Terraform-as-source-of-truth exists to
  prevent: two places (Terraform state and the live GCS object) that could
  disagree about what clusters exist, with no single audit trail or review
  gate (PR review, `terraform plan`) on a change that directly affects which
  cluster an SRE incident gets routed to.
- **A separate, non-Terraform config-management path just for the cluster
  registry** (e.g. a small CLI/script the platform team runs directly
  against GCS) — rejected because it would fragment infrastructure
  management: every other piece of this agent's routing/IAM/deployment
  config is Terraform-owned, and a second, differently-reviewed mechanism
  for just this one file adds process complexity without a compensating
  benefit, given the fix needed was making the existing Terraform path
  actually support multiple clusters, not replacing it.
- **Leave the single-cluster limitation as documented and accepted** —
  rejected once flagged as a real, current gap (not a hypothetical one):
  several runbooks and architecture pages explicitly told a reader the
  registry gets wiped on a second cluster entry, which is a genuine
  multi-cluster capability gap for a production SRE tool expected to cover
  more than one cluster over time.

## Reason

Terraform ownership of the registry keeps cluster→project/region/namespace
mappings under the same review and audit discipline (PR, plan, apply) as
every other piece of this system's routing-relevant configuration — a wrong
entry here has real consequences (the 5-tier chain trusts it completely, with
no independent cross-check against reality beyond IAM failing at tool-call
time). Making `var.additional_clusters` a proper typed variable with plan-
time collision validation means adding a cluster is a reviewable diff, not a
silent, unreviewed GCS write that the next `apply` might undo anyway.

## Tradeoffs

- Adding a cluster in the **same** `project_b_id` needs no IAM/Gateway
  change and works end-to-end. Adding a cluster in a **different** project
  gets a `clusters.json` entry immediately, but currently **zero IAM** —
  `iac/gke-access` is hardwired to one `var.project_b_id` (a scalar, not a
  list) — and will `403` at runtime until that stack is applied a second
  time against the new project. This is a known, documented gap, not
  something this fix addressed (see [Adding a New GKE
  Cluster](runbooks/add-gke-cluster.md)).
- `clusters.json`'s schema has no field yet distinguishing "reach via direct
  GKE endpoint" from "reach via Connect Gateway" — relevant specifically for
  wiring in non-GKE/on-prem clusters (see
  [ADR-012](ADR-012-gke-remote-mcp-vs-custom-mcp.md)).
- Every `terraform apply` still fully re-renders and overwrites the object —
  by design, since Terraform must remain the sole writer — so any
  hypothetical manual edit made outside Terraform will always be lost on the
  next apply. This is the intended behavior now, not a limitation, but it's
  worth knowing if someone is tempted to hot-patch the registry directly
  during an incident.

## Related ADRs

- [ADR-004: MCP as the tool-access protocol](ADR-004-mcp-tool-access-protocol.md)
- [ADR-001: Two-project topology](ADR-001-two-project-split.md)
