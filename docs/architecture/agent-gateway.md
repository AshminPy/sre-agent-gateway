# Agent Gateway

> **Implementation Status:** IMPLEMENTED (IAP REQUEST_AUTHZ, enforcing); Model Armor CONTENT_AUTHZ NOT IMPLEMENTED/BLOCKED at the API level
> **Last Verified:** 2026-08-08 — `iac/agent/agent_gateway.tf`, live `terraform.tfvars`, `../../archive/RESOLVED_2026-08-08_MODEL_ARMOR_CONTENT_AUTHZ_TEST.md`
> **Source of Truth:** `iac/agent/agent_gateway.tf:39-125`
> **Owner:** SRE Agent platform team.
>
> **⚠️ Known doc-vs-code discrepancy**: A comment in `iac/agent/monitoring.tf:361-367` (and a Model Armor comment in `iac/agent/model_armor.tf:6-7`) describes the gateway's IAP extension as running in **DRY_RUN**. This is stale. The live `iac/agent/terraform.tfvars:11` sets `iap_iam_enforcement_mode = null`, which per the variable's own definition means **ENFORCE**, not DRY_RUN — switched 2026-07-14 per that line's own comment. Treat the gateway as enforcing today.

## Why Agent Gateway exists

Agent Gateway is a Google-managed egress-governance layer purpose-built for AI agent workloads. Instead of every agent making direct outbound calls to whatever APIs/MCP servers it needs (which would need per-destination network/firewall/IAM plumbing, and would be hard to audit centrally), the agent routes all its egress through one gateway, which authorizes each request against a central registry and can (in principle — see the Model Armor caveat below) inspect content.

## Why the agent does not call every MCP server directly

Two reasons: (1) it gives one centralized authorization point instead of per-destination IAM sprawl, and (2) it's the mechanism Google's Agent Identity model expects — Agent Identity's whole value proposition (see [Agent Identity](agent-identity.md)) depends on the gateway being the thing that authorizes egress on the identity's behalf.

## How the agent authenticates to Agent Gateway

Via its Agent Identity — the SPIFFE-format principal issued to the specific reasoning-engine resource (see [Agent Identity](agent-identity.md)). There is no separate credential specifically for the gateway.

## How Agent Gateway authenticates/authorizes the request

Through an **IAP REQUEST_AUTHZ** extension and policy (`google_network_services_authz_extension.iap` + `google_network_security_authz_policy.iap`, `iac/agent/agent_gateway.tf:78-125`). This is **header/attribute-based authorization** — it checks the calling identity's IAM against the target, it does **not** TLS-terminate or inspect the actual payload content (that would be Model Armor's job, and that path isn't wired — see below).

- **Live enforcement mode**: ENFORCE (see the discrepancy callout above).
- **Fail-open behavior**: `authz_fail_open = true` (live, `iac/agent/terraform.tfvars:10`). **Operational meaning**: if the IAP authorization extension itself becomes unreachable, the gateway **allows** the request through rather than blocking it. This is a deliberate rollout-safety tradeoff (per the variable's own description), not an oversight — but it is a real risk-acceptance decision worth an explicit sign-off from Security/Risk if that hasn't already happened. It means an IAP outage silently degrades to "unauthorized egress allowed" for the outage's duration, rather than "agent stops working."
- **Egress permission scope**: the agent identity is granted `roles/iap.egressor`, scoped to the **Agent Registry** resource specifically (registry-wide, not project-wide) — `iac/agent/iap_egressor.tf:13-21`. This means the agent can only egress to destinations that are actually **registered** in the Agent Registry (see [MCP Architecture](mcp-architecture.md#agent-registry)).

## How MCP servers are registered / how MCP tools are discovered

See [MCP Architecture](mcp-architecture.md#agent-registry) — two registration patterns (`no-spec` for plain API endpoints, `tool-spec` for real MCP servers), both run via post-`terraform apply` scripts, not via the Terraform provider itself (the provider doesn't yet support this).

## How permissions are enforced

Registry-scoped `iap.egressor` + the IAP REQUEST_AUTHZ policy at the gateway, plus (independently, one layer further in) the agent's own tool allowlists and Kubernetes RBAC where applicable. There is deliberately no single point of enforcement — see [Authorization and Permissions](../governance/security.md) for the full layered picture.

## What is logged

The gateway itself does not currently emit an application-observable signal into the agent's own structured logs — this is a documented, explicit gap (see `iac/agent/monitoring.tf:361-367`'s reasoning for skipping a gateway-failure alert). The `agent_gateway_authz_mode` field in every RCA observability log entry is a permanent `None` placeholder for exactly this reason (`agent/nodes/rca_builder.py:196-205`, `agent/main.py:551-558`). **STATUS: PLANNED** — gateway-level audit logging as a distinct, queryable source is not built yet.

## How to troubleshoot Gateway failures

See [Gateway Failure runbook](../runbooks/gateway-failure.md).

## How certificates/TLS work — the atomic-PATCH mechanism

This is a real historical bug worth understanding, because the fix is unusual and easy to accidentally undo.

The reasoning engine's `agent_gateway_config` field is **not yet exposed by the Terraform Google provider**. Binding the engine to the gateway is done out-of-band via a script (`scripts/attach_gateway_to_engine.sh`) that issues a direct REST `PATCH` to the Reasoning Engine API.

**The historical failure mode**: a standalone PATCH containing *only* the gateway config succeeded at the API level, but never triggered the pipeline that bakes the gateway's dynamically-provisioned, self-signed TLS-inspection root CA into the engine's trust store — every subsequent outbound Vertex AI call then failed with `SSLError: certificate verify failed: self-signed certificate in certificate chain`.

**The fix**: the PATCH must bundle the gateway config **together with** a full re-upload of the source code (`spec.sourceCodeSpec`) in one atomic call — this is what actually triggers the trust-store update. `scripts/attach_gateway_to_engine.sh` does this correctly today, and must be **re-run after any `terraform apply` that touches the engine resource**, because the binding is not tracked in Terraform state and gets silently wiped by any engine update.

**Do not** write a "simpler" version of this script that only PATCHes the gateway config — that is exactly the bug this fixes.

## What network path is used

Google's own backbone, per the gateway's `AGENT_TO_ANYWHERE` governed access path. No customer VPC / PSC network attachment is currently used or needed for the live traffic pattern (Google APIs + GKE Remote MCP) — a previously-added PSC-Interface attachment was confirmed unnecessary and removed 2026-08-07 (`iac/agent/agent_gateway.tf:16-38`).

## What audit trail exists

Application-level: `sre-agent-tool-failures`, `sre-agent-routing-failures` Cloud Logging entries (neither is gateway-specific — see [Logging](../operations/logging.md)). Gateway-specific audit logging is a gap — see "What is logged" above.

## Where to find gateway config/logs/metrics

- **Config**: `iac/agent/agent_gateway.tf`, live values in `iac/agent/terraform.tfvars`.
- **Terraform outputs**: `agent_gateway_id`, `model_armor_request_template`, `model_armor_response_template`, `gke_remote_mcp_url`, `custom_mcp_url` — `iac/agent/outputs.tf:44-47,64-82`.
- **Gateway logs/metrics**: not currently a distinct, documented source — see the gap noted above.

## Model Armor CONTENT_AUTHZ — confirmed not wired, and why

Several places in this repo's comments (`iac/agent/model_armor.tf`, `docs/ADR-002-agent-identity-and-gateway.md`) describe or assume a Model Armor `CONTENT_AUTHZ` chain exists at the gateway alongside the IAP `REQUEST_AUTHZ` one. **It does not exist.** A direct attempt to add it (mirroring the working IAP pattern exactly, `service = "modelarmor.googleapis.com"`) failed at `terraform apply` with:

```
Error 400: The request was invalid: unsupported Google API for AuthzExtension: modelarmor.googleapis.com
```

(`../../archive/RESOLVED_2026-08-08_MODEL_ARMOR_CONTENT_AUTHZ_TEST.md:127-136`). This is an API-level rejection, not a config mistake — there is currently no known Terraform-expressible way to wire Model Armor content inspection into Agent Gateway's `AuthzExtension` mechanism. See [Security Operations](../governance/security.md#model-armor) for the full governance picture, including the app-level fallback that's also currently inactive.

---

**Related pages:** [Agent Identity](agent-identity.md) · [MCP Architecture](mcp-architecture.md) · [Security Operations](../governance/security.md) · [Gateway Failure runbook](../runbooks/gateway-failure.md)
