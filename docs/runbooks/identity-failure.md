# Runbook: Agent Identity and IAM Failures

> **Last Verified:** 2026-08-08 · **Owner:** SRE Agent platform team

## 6. Agent Identity failure

**Symptom**: the agent can't authenticate to any GCP service at all — total failure, not scoped to one resource.

**Likely causes**: `identity_type` was accidentally changed away from `AGENT_IDENTITY` in Terraform; the reasoning engine resource was recreated and the new instance's principal string (which embeds the engine's numeric ID) doesn't match what's granted in IAM bindings yet.

**How to verify**:
```bash
gcloud ai reasoning-engines describe <ENGINE_ID> \
  --project=sreagent-t2-demo --region=us-central1 --format="value(spec.identityType)"
```
Should return `AGENT_IDENTITY`. Also confirm the current engine's numeric ID matches what's referenced in `iac/agent/main.tf`'s principal construction (`agent_identity_member`) — if the engine was recreated, `terraform apply` needs to re-run to update every IAM binding that references the old engine-specific principal.

**Resolution**: `terraform apply` in `iac/agent/` to reconcile IAM bindings against the current engine ID. See [Agent Identity](../architecture/agent-identity.md).

**Escalation**: if `identity_type` itself is wrong, this requires re-creating the reasoning engine (it can't be changed in place) — treat as a significant change, get sign-off before applying.

## 7. IAM permission denied

**Symptom**: a specific tool call fails with a `403`/`PERMISSION_DENIED`.

**Likely causes**: a missing role on the target project (see the full matrix in [Authorization and Permissions](../governance/security.md)); a role that exists but is scoped to the wrong resource.

**How to verify**:
```bash
gcloud projects get-iam-policy sreagent-demo \
  --flatten="bindings[].members" \
  --filter="bindings.members:principal://agents.global.org-*"
```
(Project B — the target GKE project — is where cross-project grants live, `iac/gke-access/crossproject_iam.tf`.) Confirm `roles/container.viewer`, `roles/mcp.toolUser`, `roles/logging.viewer`, `roles/monitoring.viewer` are all present.

**Resolution**: if a grant is genuinely missing, add it via Terraform in `iac/gke-access/` (never manually via `gcloud` for anything permanent — see [Terraform / Infrastructure Management](../operations/terraform.md)) and get it reviewed — this touches production IAM.

**Escalation**: if all expected grants are present and the call still fails, check whether the resource being accessed is outside what those roles cover (e.g., a `list_nodes` call against a cluster whose RBAC binding doesn't cover cluster-scoped Nodes — a known, documented read-availability gap, see [GKE vs Non-GKE Access](../architecture/gke-vs-nongke.md)).

---

**Related pages:** [Agent Identity](../architecture/agent-identity.md) · [Authorization and Permissions](../governance/security.md) · [Gateway Failure](gateway-failure.md)
