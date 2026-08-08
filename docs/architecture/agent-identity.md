# Agent Identity

> **Implementation Status:** IMPLEMENTED
> **Last Verified:** 2026-08-08 — `iac/agent/agent_engine.tf`, `iac/agent/main.tf`, `iac/agent/iam.tf`
> **Source of Truth:** `iac/agent/agent_engine.tf:94-98` (`identity_type = "AGENT_IDENTITY"`), `iac/agent/main.tf:19` (principal construction)
> **Owner:** SRE Agent platform team.

## What Agent Identity is

A platform-managed, per-workload identity that Vertex AI Agent Engine issues automatically for a specific reasoning-engine resource — no human creates it, no key file exists for it. It's expressed as a SPIFFE-format principal string that Google's IAM system understands, and you grant it permissions the same way you'd grant permissions to any other principal (a user, a group, a service account).

## Why we chose Agent Identity

Set explicitly in Terraform: `identity_type = "AGENT_IDENTITY"` (`iac/agent/agent_engine.tf:94-98`), with an inline comment stating this is **mutually exclusive** with a traditional `service_account` field, which must not be set. This was a deliberate choice, not a default we happened to inherit.

## How it differs from a traditional service account

| | Traditional Service Account | Agent Identity |
|---|---|---|
| Key material | Can have a downloadable JSON key (a real security liability if leaked) | No key material exists, ever — this org additionally has `constraints/iam.disableServiceAccountKeyCreation` enforced, confirmed by a real `FAILED_PRECONDITION` when key creation was attempted during testing |
| Scope | Whatever you grant it — often over-scoped in practice because it's reusable across workloads | Scoped to exactly one reasoning-engine resource by construction — the principal string embeds the specific engine's numeric ID |
| Credential lifetime | Long-lived by default unless you build key rotation yourself | Short-lived, platform-managed, auto-refreshed |
| Auth mechanism for GCP API calls | Bearer token (OAuth) typically | Defaults to mTLS/X.509 per Google's own documentation |

## Why this matters for AI agents specifically

An AI agent making autonomous tool calls is exactly the kind of workload where a leaked long-lived credential is disproportionately dangerous — it can't "notice" it's being misused the way a human might. Agent Identity removes the leak surface (no key file to leak) and keeps the blast radius scoped to one specific deployed agent, not a reusable credential shared across services.

## How Agent Engine obtains identity

Automatically, on deployment, because `identity_type = "AGENT_IDENTITY"` is set — this is a platform mechanism, not something the application code participates in.

## How that identity is presented to Agent Gateway

The agent's outbound calls carry this identity; Agent Gateway's IAP `REQUEST_AUTHZ` extension checks it against the target's IAM policy (registry-level `iap.egressor` grant — see [Agent Gateway](agent-gateway.md)) before allowing the request through.

## How downstream authorization works

Standard GCP IAM: the principal string is used as the `member` in every IAM binding relevant to the agent (see the full matrix in [Authorization and Permissions](../governance/security.md)). There's nothing special about how downstream services check it once it's presented — it's IAM the whole way.

## SPIFFE / workload identity concepts

The principal format is SPIFFE-based:
```
principal://agents.global.org-${org_id}.system.id.goog/resources/aiplatform/projects/${project_number}/locations/${region}/reasoningEngines/${engine_numeric_id}
```
(`iac/agent/main.tf:19`) — every component (`org_id`, `project_number`, `engine_numeric_id`) is sourced from live Terraform data, never hardcoded, so the principal string is always correct for whatever project/engine it's actually deployed against.

A broader **principalSet** form also exists (org-wide, any Agent Engine agent in this project, not scoped to one specific engine ID) — used for the cross-project GKE-access grant (`iac/agent/main.tf:24`, re-derived in `iac/gke-access/main.tf:11`), since that grant needs to work even across an engine recreation (which would otherwise change the specific engine ID in the narrower principal).

## Whether credentials ever exist inside application code

**No.** Confirmed by inspection: no `google_service_account_key` resource exists anywhere in this repo's Terraform, no key material is git-tracked (`.gitignore` explicitly excludes `.env`/`terraform.tfvars`/state files, and this was verified — only `.example` files are tracked), and the agent's own code obtains tokens dynamically via `google.auth.default()` (ADC) and `google.oauth2.id_token.fetch_id_token()` — never a static credential.

## Credential lifetime / rotation

Short-lived, platform-managed. Not something the application or Terraform configures directly — this is inherent to how Agent Identity works.

## Auditing

Standard Cloud Audit Logs capture IAM-relevant API calls made using this identity, same as any other principal. See the Connect Gateway audit-logging gap noted in [GKE vs Non-GKE Access](gke-vs-nongke.md) for one specific, known exception where successful reads aren't captured by default.

## Least privilege / blast-radius reduction

See the full permission matrix in [Authorization and Permissions](../governance/security.md) — every grant is resource-scoped where the resource type supports it (buckets, the specific Cloud Run service, the Agent Registry) rather than project-wide, and every grant has a stated justification in code comments.

## Current Agent Identity resource, IAM bindings, roles, and Terraform locations

| What | Where |
|---|---|
| The `identity_type` setting | `iac/agent/agent_engine.tf:94-98` |
| The principal-string construction | `iac/agent/main.tf:19,24` |
| Project A (own project) IAM bindings | `iac/agent/iam.tf:8-30` |
| Project B (target GKE) cross-project IAM bindings | `iac/gke-access/crossproject_iam.tf:9-23` |
| Registry-scoped gateway egress grant | `iac/agent/iap_egressor.tf:13-21` |

We do **not** repeat any older service-account-based design in the docs above — the historical Service Account architecture this project may once have used is not the current implementation and should not be described as such anywhere in this knowledge base.

---

**Related pages:** [Agent Gateway](agent-gateway.md) · [Authorization and Permissions](../governance/security.md) · [AI Governance](../governance/ai-governance.md)
