# Disaster Recovery and Continuity

> **Implementation Status:** PARTIALLY IMPLEMENTED — most core state is rebuildable from Terraform/git; no tested full-DR runbook exists
> **Last Verified:** 2026-08-08
> **Owner:** SRE Agent platform team.

## What happens if the Agent Engine deployment is lost

Fully rebuildable: `terraform apply` in `iac/agent/` recreates the reasoning engine, then `scripts/attach_gateway_to_engine.sh` re-binds it to the gateway. The one thing to know: recreating the engine changes its numeric ID, which is embedded in the Agent Identity principal string — every IAM binding referencing that principal (in both `iac/agent/` and `iac/gke-access/`) needs a `terraform apply` in both stacks to reconcile.

## What can be rebuilt from Terraform

Everything under `iac/`: the engine, gateway, IAM, Model Armor templates, monitoring, buckets (empty — see below for data), networking, CI/CD identity.

## What information is durable vs. ephemeral

| | Durable | Recovery source |
|---|---|---|
| Evidence (raw tool output) | Yes, 90-day retention | GCS evidence bucket — `prevent_destroy=true`, survives a `terraform apply` cycle |
| RCA records | Yes, 365-day retention | GCS eval bucket — same protection |
| Memory Bank entries | Yes | Vertex AI Memory Bank resource — a **separate** reasoning engine (`memory_bank`), also recreatable from Terraform, but its *content* (past memories) is not restorable from Terraform if the resource itself is deleted — Terraform only recreates the empty resource |
| In-process fallback memory (20-entry list) | **No** | Lost on every container restart — this was never meant to be the durable store |
| `AgentState` (per-investigation) | No | Not meant to be durable — exists only for one `graph.invoke()` call |
| `clusters.json` | Yes, but fragile | GCS cluster-config bucket, but content is regenerated (and any manual multi-cluster edits wiped) on every `terraform apply` — see [Cluster Routing](../architecture/cluster-routing.md) |

## GCS recovery

Both evidence and eval buckets are versioned — a bad overwrite/delete can be recovered from an object's prior version, not just from a backup process. `prevent_destroy=true` on both bucket resources means a `terraform destroy` cannot silently remove them either.

## Terraform state recovery

State lives in a GCS backend bucket (`sreagent-t2-demo-tfstate`) — standard GCS durability applies. There is no documented state-recovery drill in this repo — **STATUS: PLANNED**, worth a tabletop exercise before relying on this in a real incident.

## MCP recovery

GKE Remote MCP: nothing to recover, it's Google-managed. Custom MCP: rebuildable from Terraform + the container image in Artifact Registry (tagged by git SHA, so any prior version is redeployable) — but see [MCP Architecture](../architecture/mcp-architecture.md) for why it's not live today regardless.

## Configuration recovery

Everything Terraform-managed is recoverable by re-applying from git history. Anything intentionally out-of-band (the gateway-engine binding) needs its script re-run, not a Terraform apply.

## Rollback/redeploy process

See [Rollback](rollback.md).

## What's genuinely untested

There is no evidence in this repo of a full disaster-recovery drill having been run (e.g., "delete the whole project, rebuild from scratch, time it"). Treat the "everything is rebuildable from Terraform" claim above as **true in principle, unverified in practice** until such a drill happens.

---

**Related pages:** [Terraform / Infrastructure Management](terraform.md) · [Rollback](rollback.md) · [Reliability and Failure Modes](../governance/reliability.md)
