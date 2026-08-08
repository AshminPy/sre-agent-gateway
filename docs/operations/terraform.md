# Terraform / Infrastructure Management

> **Implementation Status:** IMPLEMENTED
> **Last Verified:** 2026-08-08 — `iac/agent/*.tf`
> **Owner:** SRE Agent platform team.

## Repository layout

All infrastructure lives under `iac/`, split into two Terraform root modules:

| Directory | Purpose |
|---|---|
| `iac/agent/` | The main agent stack — Reasoning Engine, Agent Gateway, IAM, Model Armor, monitoring, buckets, networking, CI/CD identity |
| `iac/gke-access/` | Cross-project IAM granting the agent identity read-only access into the target GKE project |

## Environments

One live environment today: GCP project `sreagent-t2-demo` (agent stack) + `sreagent-demo` (target GKE, project B). There is no separate staging/dev Terraform environment configured in this repo — `iac/agent/terraform.tfvars.example` is the template for standing up an equivalent environment elsewhere.

## Component inventory (`iac/agent/*.tf`)

| File | Purpose |
|---|---|
| `agent_engine.tf` | The core reasoning engine + companion Memory Bank engine; runtime env vars |
| `agent_gateway.tf` | Agent Gateway + IAP REQUEST_AUTHZ |
| `apis.tf` | Enables required Google Cloud APIs |
| `backend.tf` | GCS remote state backend declaration |
| `buckets.tf` | Evidence, eval, and cluster-config GCS buckets |
| `cloudrun_mcp.tf` | Optional custom Cloud Run MCP (currently disabled live) |
| `iam.tf` | Least-privilege IAM for the runtime identity + platform service agents |
| `iap_egressor.tf` | Registry-scoped `iap.egressor` grant |
| `main.tf` | Core data lookups, Agent Identity principal construction |
| `model_armor.tf` | Model Armor templates (request/response) |
| `monitoring.tf` | Log-based metrics, alert policies, notification channel |
| `networking.tf` | Agent-side VPC, subnet, Cloud NAT for egress |
| `oidc.tf` | CI/CD deployer identity (WIF, no long-lived keys) |
| `outputs.tf` | Terraform outputs consumed by scripts and `.env` generation |
| `providers.tf` | Provider blocks |
| `variables.tf` | All input variables |
| `versions.tf` | Provider version pins |
| `terraform.tfvars` | Live values — gitignored, present only locally/in CI secrets |

## Variables

Required: `project_a_id`, `project_b_id`, `notification_email`, `github_repo`, `tfstate_bucket`. Feature flags: `enable_agent_gateway`, `create_wif`, `enable_custom_mcp`, `iap_iam_enforcement_mode`, `authz_fail_open`. See `iac/agent/variables.tf` for the complete, current list.

## State backend

GCS, bucket `sreagent-t2-demo-tfstate`, prefix `agent` for the main stack. Declared generically in `backend.tf`; the actual bucket name is passed at `terraform init` time via `-backend-config`, both locally and in CI.

## Deployment order / dependencies

`iac/agent/` and `iac/gke-access/` are independent stacks with no automatic ordering enforced between them — in practice, `iac/agent/` (which creates the Agent Identity) should be applied before `iac/gke-access/` (which grants that identity permissions in project B), since the latter references the former's identity output.

## What must NEVER be changed manually

- The gateway-to-engine binding — this is **intentionally** out-of-band (not tracked in Terraform state, managed by `scripts/attach_gateway_to_engine.sh`) — don't try to "fix" this by adding it to Terraform; the provider doesn't support it yet.
- `clusters.json` in the cluster-config bucket — it IS Terraform-managed today (which is itself the source of the wipe-on-apply limitation — see [Cluster Routing](../architecture/cluster-routing.md)); manual edits will be silently reverted on the next apply.
- Anything in the evidence/eval buckets — both have `prevent_destroy = true` specifically to prevent accidental data loss.
- IAM bindings, ever, without going through a reviewed PR — this is the security-sensitive surface most likely to cause real harm if hand-edited.

## Drift detection and recovery

No automated drift-detection job exists in this repo (e.g., a scheduled `terraform plan` with alerting on non-empty diff) — **STATUS: PLANNED/not built**. Drift is currently only caught the next time someone runs `terraform plan` manually or via CI. See [Terraform Drift runbook](../runbooks/deployment-failure.md#30-terraform-drift).

---

**Related pages:** [Updating the Agent](deployment.md) · [Terraform Drift runbook](../runbooks/deployment-failure.md) · [Disaster Recovery](disaster-recovery.md)
