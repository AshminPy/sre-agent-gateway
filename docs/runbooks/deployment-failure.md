# Runbook: Deployment and CI/CD Failures

> **Last Verified:** 2026-08-08 · **Owner:** SRE Agent platform team

## 2. Agent deployment failure

**Symptom**: `terraform apply` fails, or the CI `terraform-apply.yml` job fails.

**How to verify**: read the CI job log in order — `package agent` → auth → `terraform init` → `apply` #1 → (conditional MCP image build/apply #2) → register endpoints → attach gateway (self-heals once) → smoke test. Identify which step failed.

**Resolution by step**:
- **Package/init/apply #1 fails**: standard Terraform error — read the message, check for a real config issue or drift (see [Terraform Drift](#30-terraform-drift) below).
- **Attach-gateway step fails with `"The Reasoning Engine failed to be updated"`**: this is the known, self-healing flake — CI automatically recreates the engine and retries once. If it fails a *second* time (after the auto-retry), something else is wrong — escalate.
- **Attach-gateway step fails with a different error**: does not auto-retry by design — read the message directly, likely a real config/permissions issue.
- **Smoke test fails**: the deploy technically succeeded but the agent doesn't actually work end-to-end — do not consider this deployment successful. See [Investigation-Level Failures](investigation-failure.md).

## 28. Deployment regression

**Symptom**: a deploy went through clean, but agent behavior got worse afterward (higher escalation rate, more errors, slower).

**How to verify**: compare `sre_agent/escalations`, `sre_agent/errors`, `sre_agent/investigation_latency_seconds` before/after the deploy timestamp. Run the golden eval suite against the newly-deployed engine: `python -m agent.eval.run_eval --mode remote --engine-id <ENGINE_ID>` (see [Evaluation](../architecture/evaluation.md)) — note this is **not** automatically run in CI today, so a regression like this can genuinely slip through un-caught.

**Resolution**: see [Rollback](../operations/rollback.md).

## 29. CI/CD failure

**Symptom**: the GitHub Actions workflow itself fails (not a Terraform/deployment logic failure — e.g., auth/WIF failure, missing secret).

**How to verify**: check the specific step — WIF auth failures usually mean the `google-github-actions/auth@v2` step's `workload_identity_provider`/`service_account` secrets are misconfigured or the CI deployer SA's WIF binding drifted.

**Resolution**: see [CI/CD](../operations/deployment.md#cicd) for the full pipeline; the deployer identity setup itself is `scripts/bootstrap_wif.sh` (one-time, out-of-band — not something CI can fix itself, chicken-and-egg).

## 30. Terraform drift

**Symptom**: `terraform plan` shows unexpected changes with no corresponding code change.

**Likely causes**: someone made a manual change via `gcloud`/Console (against policy — see [Terraform / Infrastructure Management](../operations/terraform.md)); a platform-side default changed; the gateway-engine binding (which is legitimately out-of-band, not tracked in state — this is *expected* "drift" that isn't really drift, see [Agent Gateway](../architecture/agent-gateway.md)).

**How to verify**: `terraform plan` and read the diff carefully — distinguish "someone touched this manually" from "this resource is legitimately managed outside Terraform by design" (the gateway attachment is the one known, intentional case).

**Resolution**: if it's a real manual change, decide whether to import it into Terraform or revert it via `apply`. Never leave real drift unresolved — the next apply's behavior becomes unpredictable.

**Escalation**: any drift involving IAM or the Model Armor/gateway configuration should get a second reviewer before reconciling — these are the security-sensitive surfaces.

---

**Related pages:** [Updating the Agent](../operations/deployment.md) · [Rollback](../operations/rollback.md) · [Terraform / Infrastructure Management](../operations/terraform.md)
