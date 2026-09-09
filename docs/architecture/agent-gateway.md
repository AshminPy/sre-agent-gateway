# Agent Gateway

> **Implementation Status:** IMPLEMENTED (IAP REQUEST_AUTHZ, enforcing, fail-closed); Model Armor CONTENT_AUTHZ IMPLEMENTED and wired, with a known permanent platform limitation for MCP tool-response bodies — see below
> **Last Verified:** 2026-09-07 — `iac/agent/agent_gateway.tf`, `iac/agent/model_armor.tf`, live `terraform.tfvars`, `iac/agent/variables.tf`
> **Source of Truth:** `iac/agent/agent_gateway.tf:39-125`
> **Owner:** SRE Agent platform team.
>
> **⚠️ Known doc-vs-code discrepancy**: A comment in `iac/agent/monitoring.tf:361-367` (and a Model Armor comment in `iac/agent/model_armor.tf:6-7`) describes the gateway's IAP extension as running in **DRY_RUN**. This is stale. The live `iac/agent/terraform.tfvars:11` sets `iap_iam_enforcement_mode = null`, which per the variable's own definition means **ENFORCE**, not DRY_RUN — switched 2026-07-14 per that line's own comment. Treat the gateway as enforcing today.

## Why Agent Gateway exists

Agent Gateway is a Google-managed egress-governance layer purpose-built for AI agent workloads. Instead of every agent making direct outbound calls to whatever APIs/MCP servers it needs (which would need per-destination network/firewall/IAM plumbing, and would be hard to audit centrally), the agent routes all its egress through one gateway, which authorizes each request against a central registry and inspects content via Model Armor (see the coverage caveat below).

## Why the agent does not call every MCP server directly

Two reasons: (1) it gives one centralized authorization point instead of per-destination IAM sprawl, and (2) it's the mechanism Google's Agent Identity model expects — Agent Identity's whole value proposition (see [Agent Identity](agent-identity.md)) depends on the gateway being the thing that authorizes egress on the identity's behalf.

## How the agent authenticates to Agent Gateway

Via its Agent Identity — the SPIFFE-format principal issued to the specific reasoning-engine resource (see [Agent Identity](agent-identity.md)). There is no separate credential specifically for the gateway.

## How Agent Gateway authenticates/authorizes the request

Through an **IAP REQUEST_AUTHZ** extension and policy (`google_network_services_authz_extension.iap` + `google_network_security_authz_policy.iap`, `iac/agent/agent_gateway.tf:78-125`). This is **header/attribute-based authorization** — it checks the calling identity's IAM against the target; separately, a Model Armor **CONTENT_AUTHZ** extension is also wired at the gateway to inspect payload content (`google_network_services_authz_extension.model_armor`, `iac/agent/agent_gateway.tf`) — see the "Model Armor CONTENT_AUTHZ" section below for what it does and does not cover.

- **Live enforcement mode**: ENFORCE (see the discrepancy callout above).
- **Fail-open behavior**: `authz_fail_open = false` (live, `iac/agent/variables.tf` — default changed `true`→`false` 2026-09-05, PR #249). **Operational meaning**: if the IAP authorization extension itself becomes unreachable, the gateway **blocks** the request (fail-closed) rather than allowing it through. This replaces the earlier rollout-safety fail-open default; an IAP outage now degrades to "agent stops working" rather than "unauthorized egress allowed."
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

## Model Armor CONTENT_AUTHZ — wired and live, with a known permanent platform limitation

The gateway has a Model Armor `CONTENT_AUTHZ` extension wired alongside the IAP `REQUEST_AUTHZ` one: `google_network_services_authz_extension.model_armor` (`iac/agent/agent_gateway.tf`, `policy_profile = "CONTENT_AUTHZ"`), referencing the `sre_agent_request`/`sre_agent_response` Model Armor templates (`iac/agent/model_armor.tf`, `enforcement_type = "INSPECT_AND_BLOCK"`). Separately, Model Armor **floor settings** (`google_model_armor_floorsetting`, same file) are live project-wide with `inspect_only = true` (inspect, not block) and HIGH confidence for malicious-URI detection.

**Known, permanent limitation — not a bug, not fixed by this wiring**: Google's platform does not invoke `RESPONSE_BODY`/content inspection for MCP tool-response traffic over Streamable HTTP transport. This means the gateway's CONTENT_AUTHZ inspection does not cover the custom MCP's tool-response bodies. This is an accepted platform limitation, not something this repo can configure around at the gateway layer.

**Compensating control**: for the custom MCP path specifically, an application-level response guard (`mcp/response_guard.py`, live-deployed) inspects tool responses before they reach the agent. This compensates for the gateway gap on that one path — it does not close the underlying platform limitation itself, which remains open for any other traffic the gateway can't invoke RESPONSE_BODY inspection for. See [Security Operations](../governance/security.md#model-armor) for the full governance picture.

---

**Related pages:** [Agent Identity](agent-identity.md) · [MCP Architecture](mcp-architecture.md) · [Security Operations](../governance/security.md) · [Gateway Failure runbook](../runbooks/gateway-failure.md)
