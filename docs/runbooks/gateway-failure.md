# Runbook: Agent Gateway Failures

> **Last Verified:** 2026-08-08 · **Owner:** SRE Agent platform team

## 1. Agent Gateway unavailable

**Symptom**: investigations fail or hang; tool calls that should route through the gateway never complete.

**Likely causes**: gateway resource in a bad state; a recent `terraform apply` recreated the gateway without a completed re-attach; regional Google outage.

**How to verify**:
```bash
gcloud beta network-services agent-gateways describe sre-agent-egress \
  --project=sreagent-t2-demo --location=us-central1
```
Check `agentGatewayCard` is fully populated (mtlsEndpoint, serviceExtensionsServiceAccount, rootCertificates present) — this is the best available readiness signal; there's no explicit state field on this resource type.

**Expected output**: a populated `agentGatewayCard`. Empty/partial = gateway not ready.

**Resolution**: if the gateway was recently recreated, re-run `bash scripts/attach_gateway_to_engine.sh` — see [Agent Gateway](../architecture/agent-gateway.md#how-certificatestls-work--the-atomic-patch-mechanism) for why this is required after every engine-touching apply. If the gateway itself looks unhealthy and wasn't recently touched, check GCP status dashboard for a regional incident.

**Escalation**: if `attach_gateway_to_engine.sh` fails with `code: 3, "The Reasoning Engine failed to be updated"` — this is a known, recurring, self-healed-in-CI flake (recreate the engine and retry once, `.github/workflows/terraform-apply.yml`'s attach step does this automatically). If it fails with a different error, escalate to the platform team — do not blindly retry.

## 2. Gateway authentication failure

**Symptom**: requests through the gateway return an authorization error.

**Likely causes**: the Agent Identity's `roles/iap.egressor` binding is missing or scoped wrong; the target isn't registered in the Agent Registry.

**How to verify**:
```bash
gcloud iap web get-iam-policy \
  --resource-type=agent-registry --project=sreagent-t2-demo
```
Confirm the agent's principal has `roles/iap.egressor`. Also confirm the target endpoint is registered:
```bash
gcloud alpha agent-registry services list --project=sreagent-t2-demo --location=<region>
```

**Resolution**: re-run `scripts/register_endpoints.py` (and `scripts/register_custom_mcp.py` if the custom MCP is in use) to (re-)register missing endpoints — see [Agent Gateway](../architecture/agent-gateway.md#how-mcp-servers-are-registered--how-mcp-tools-are-discovered).

**Escalation**: if the IAM binding is present and correct and the target is registered, but auth still fails — check `authz_fail_open`/`iap_iam_enforcement_mode` in the live tfvars (see [Agent Gateway](../architecture/agent-gateway.md)) haven't drifted from what's documented, then escalate to platform team.

## 3. TLS / certificate failure

**Symptom**: `SSLError: certificate verify failed: self-signed certificate in certificate chain` on outbound Vertex AI calls.

**Likely cause**: the gateway was bound to the engine via a PATCH that did **not** bundle a fresh source-code upload — this is the exact historical bug documented in [Agent Gateway](../architecture/agent-gateway.md#how-certificatestls-work--the-atomic-patch-mechanism).

**How to verify**: check whether `attach_gateway_to_engine.sh` was run after the most recent engine-touching `terraform apply`. If someone manually PATCHed just the gateway config (bypassing the script), that's almost certainly the cause.

**Resolution**: re-run `bash scripts/attach_gateway_to_engine.sh` — it always bundles a full source re-upload with the gateway config in one atomic call, which is what actually triggers the trust-store update.

**Escalation**: if the script itself fails or the error persists after a clean re-run, escalate to platform team — this indicates either an engine-side state problem (recreate the engine, per the self-heal pattern in CI) or a genuine Google-side issue.

---

**Related pages:** [Agent Gateway](../architecture/agent-gateway.md) · [Identity/IAM Failure](identity-failure.md)
