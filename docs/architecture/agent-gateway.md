# Agent Gateway

> **Implementation Status:** IMPLEMENTED — IAP REQUEST_AUTHZ (enforcing) AND Model Armor CONTENT_AUTHZ (enforcing on the request side; response side has a confirmed Google platform limitation — see below).
> **Last Verified:** 2026-09-06 — `iac/agent/agent_gateway.tf`, `iac/agent/model_armor.tf`, real gateway logs
> **Source of Truth:** `iac/agent/agent_gateway.tf:39-125`, `iac/agent/model_armor.tf`
> **Owner:** SRE Agent platform team.
>
> **Corrected 2026-09-06**: this page previously said CONTENT_AUTHZ could not be wired at all (API rejection). That was accurate for the 2026-08-08 attempt, which used the wrong service hostname (`modelarmor.googleapis.com`). The correct **regional** hostname (`modelarmor.{region}.rep.googleapis.com`) works — CONTENT_AUTHZ is real and live. See the rewritten section below.
>
> **Known doc-vs-code discrepancy, still open**: A comment in `iac/agent/monitoring.tf:361-367` describes the gateway's IAP extension as running in **DRY_RUN**. This is stale. The live `iac/agent/terraform.tfvars` sets `iap_iam_enforcement_mode = null`, which per the variable's own definition means **ENFORCE**, not DRY_RUN — switched 2026-07-14. Treat the gateway as enforcing today.

## Why Agent Gateway exists

Agent Gateway is a Google-managed egress-governance layer purpose-built for AI agent workloads. Instead of every agent making direct outbound calls to whatever APIs/MCP servers it needs (which would need per-destination network/firewall/IAM plumbing, and would be hard to audit centrally), the agent routes all its egress through one gateway, which authorizes each request against a central registry and can (in principle — see the Model Armor caveat below) inspect content.

## Why the agent does not call every MCP server directly

Two reasons: (1) it gives one centralized authorization point instead of per-destination IAM sprawl, and (2) it's the mechanism Google's Agent Identity model expects — Agent Identity's whole value proposition (see [Agent Identity](agent-identity.md)) depends on the gateway being the thing that authorizes egress on the identity's behalf.

## How the agent authenticates to Agent Gateway

Via its Agent Identity — the SPIFFE-format principal issued to the specific reasoning-engine resource (see [Agent Identity](agent-identity.md)). There is no separate credential specifically for the gateway.

## How Agent Gateway authenticates/authorizes the request

Through an **IAP REQUEST_AUTHZ** extension and policy (`google_network_services_authz_extension.iap` + `google_network_security_authz_policy.iap`, `iac/agent/agent_gateway.tf:78-125`). This is **header/attribute-based authorization** — it checks the calling identity's IAM against the target, it does **not** TLS-terminate or inspect the actual payload content (that would be Model Armor's job, and that path isn't wired — see below).

- **Live enforcement mode**: ENFORCE (see the discrepancy callout above).
- **Fail-open behavior**: `authz_fail_open = true` on `main` today. **Operational meaning**: if the IAP authorization extension itself becomes unreachable, the gateway **allows** the request through rather than blocking it. A validated fail-closed fix exists on an unmerged branch (`fix/content-authz-json-response-and-fail-closed`) — see [Security Operations](../governance/security.md#iap-fail-open--fix-validated-not-yet-merged) for the live test evidence and what it does/doesn't prove.
- **Egress permission scope**: the agent identity is granted `roles/iap.egressor`, scoped to the **Agent Registry** resource specifically (registry-wide, not project-wide) — `iac/agent/iap_egressor.tf:13-21`. This means the agent can only egress to destinations that are actually **registered** in the Agent Registry (see [MCP Architecture](mcp-architecture.md#agent-registry)).

## How MCP servers are registered / how MCP tools are discovered

See [MCP Architecture](mcp-architecture.md#agent-registry) — two registration patterns (`no-spec` for plain API endpoints, `tool-spec` for real MCP servers), both run via post-`terraform apply` scripts, not via the Terraform provider itself (the provider doesn't yet support this).

## How permissions are enforced

Registry-scoped `iap.egressor` + the IAP REQUEST_AUTHZ policy at the gateway, plus (independently, one layer further in) the agent's own tool allowlists and Kubernetes RBAC where applicable. There is deliberately no single point of enforcement — see [Authorization and Permissions](../governance/security.md) for the full layered picture.

## What is logged

The gateway itself does not currently emit an application-observable signal into the agent's own structured logs — this is a documented, explicit gap (see `iac/agent/monitoring.tf:361-367`'s reasoning for skipping a gateway-failure alert). The `agent_gateway_authz_mode` field in every RCA observability log entry is a permanent `None` placeholder for exactly this reason (`agent/nodes/rca_builder.py:196-205`, `agent/main.py:551-558`). **STATUS: PLANNED** — gateway-level audit logging as a distinct, queryable source is not built yet.

## How to troubleshoot Gateway failures

See [Gateway Failure runbook](../runbooks/gateway-failure.md).

## How certificates/TLS work — the atomic-PATCH mechanism (historical; Terraform-managed since 2026-08-10)

This is a real historical bug worth understanding, because the original fix is unusual — but **since PR #93 (2026-08-10), the gateway binding is a native Terraform-managed attribute** (`google_vertex_ai_reasoning_engine.sre_agent`'s `agent_gateway_config` block, `iac/agent/agent_engine.tf`, provider `google-beta >= 7.40.0`). A normal `terraform apply` keeps the binding in place — verified live for both a fresh binding and a source-only update, zero replacement either time. `scripts/attach_gateway_to_engine.sh` (and `make attach-gateway`) still exist, unchanged, as a **manual emergency rollback tool only** — CI does not invoke it, and you should not need it for a normal deploy.

**The historical failure mode this script exists to fix, if you ever need it**: a standalone PATCH containing *only* the gateway config succeeds at the API level but never triggers the pipeline that bakes the gateway's dynamically-provisioned, self-signed TLS-inspection root CA into the engine's trust store — every subsequent outbound Vertex AI call then fails with `SSLError: certificate verify failed: self-signed certificate in certificate chain`. The fix is bundling the gateway config **together with** a full re-upload of the source code (`spec.sourceCodeSpec`) in one atomic call. Do not write a "simpler" version of this script that only PATCHes the gateway config — that is exactly the bug this fixes.

## What network path is used

Google's own backbone, per the gateway's `AGENT_TO_ANYWHERE` governed access path. No customer VPC / PSC network attachment is currently used or needed for the live traffic pattern (Google APIs + GKE Remote MCP) — a previously-added PSC-Interface attachment was confirmed unnecessary and removed 2026-08-07 (`iac/agent/agent_gateway.tf:16-38`).

## What audit trail exists

Application-level: `sre-agent-tool-failures`, `sre-agent-routing-failures` Cloud Logging entries (neither is gateway-specific — see [Logging](../operations/logging.md)). Gateway-specific audit logging is a gap — see "What is logged" above.

## Where to find gateway config/logs/metrics

- **Config**: `iac/agent/agent_gateway.tf`, live values in `iac/agent/terraform.tfvars`.
- **Terraform outputs**: `agent_gateway_id`, `model_armor_request_template`, `model_armor_response_template`, `gke_remote_mcp_url`, `custom_mcp_url` — `iac/agent/outputs.tf:44-47,64-82`.
- **Gateway logs/metrics**: not currently a distinct, documented source — see the gap noted above.

## Model Armor CONTENT_AUTHZ — real, wired, request-side only

`google_network_services_authz_extension.model_armor` + `google_network_security_authz_policy.model_armor` (`iac/agent/model_armor.tf`) mirror the IAP pattern, but with `service = "modelarmor.us-central1.rep.googleapis.com"` — the **regional** hostname. The 2026-08-08 attempt that failed with `Error 400: unsupported Google API for AuthzExtension: modelarmor.googleapis.com` used the bare (non-regional) hostname; that was the actual blocker, not a hard platform limitation as originally concluded (see `archive/RESOLVED_2026-08-08_MODEL_ARMOR_CONTENT_AUTHZ_TEST.md` for that original, now-superseded finding).

**What's real today**: genuine REQUEST-side inspection and blocking (`enforcement_type = "INSPECT_AND_BLOCK"` on both templates) — live-verified gateway logs show `REQUEST_BODY: CONTENT_MODIFIED`. **What's still a confirmed gap**: RESPONSE-side inspection never fires for MCP `tools/call` responses, on any MCP source — a documented Google platform limitation (Streamable HTTP/SSE MCP traffic is explicitly excluded from gateway sanitization). Full detail, evidence, and the unmerged application-level guard that compensates for the custom MCP specifically: [Security Operations](../governance/security.md#model-armor--three-distinct-mechanisms-each-with-different-enforcement-corrected-2026-09-06).

---

**Related pages:** [Agent Identity](agent-identity.md) · [MCP Architecture](mcp-architecture.md) · [Security Operations](../governance/security.md) · [Gateway Failure runbook](../runbooks/gateway-failure.md)
