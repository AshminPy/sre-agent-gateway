# ADR-001: Two-project topology (agent + gateway in A, GKE in B)

Status: Accepted

## Context

The SRE agent investigates GKE clusters. In most real environments the cluster
already exists and is owned by a platform/infrastructure team, while the agent is
a new workload owned by an SRE/app team. Coupling the agent's lifecycle to the
cluster's would force the two teams into one Terraform state and one project.

## Decision

Split into two independently-appliable Terraform stacks:

- **`iac/agent` (Project A)** — the agent, Agent Gateway, Model Armor, Memory
  Bank, buckets, monitoring, and CI identity. This is the primary deliverable.
- **`iac/gke-access` (Project B)** — the GKE cluster (optional; skip to use an
  existing one) plus the minimal read-only cross-project IAM the agent needs.

The agent (Project A) reaches the cluster (Project B) through the Google-managed
GKE Remote MCP endpoint (`container.googleapis.com/mcp/read-only`), passing the
target cluster's path as a parameter. Its Agent Identity is granted four
read-only roles in Project B.

To avoid an ordering dependency, the cross-project grant uses the org-wide
**principalSet** for Project A's agents (derived from Project A's project number
and org ID), not a specific engine ID — so `iac/gke-access` can be applied before
or after the agent, and works even before the agent exists.

## Consequences

- Teams with existing GKE run only the tiny grant part of `iac/gke-access`
  (`create_gke_cluster = false`) and then deploy the agent — the common case.
- Two applies instead of one, and a small amount of value passing between stacks
  (Project A's ID/number, the deployer SA email) via documented outputs/vars.
- Clear blast-radius and ownership separation between the two projects.
