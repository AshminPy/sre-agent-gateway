# Updating the Agent / CI/CD

> **Implementation Status:** IMPLEMENTED
> **Last Verified:** 2026-09-06 — `.github/workflows/terraform-apply.yml`, `.github/workflows/terraform-plan.yml`
> **Source of Truth:** `.github/workflows/terraform-apply.yml`
> **Owner:** SRE Agent platform team.

## The full deployment path

```
developer change (agent/, mcp/, or iac/agent/)
  → branch, PR
  → terraform-plan.yml runs on the PR (validate → terraform test → plan)
  → merge to main
  → terraform-apply.yml runs
```

**Correction (2026-08-09):** an earlier version of this doc claimed `terraform-plan.yml`
"posts as a PR comment" — checked directly against the workflow YAML, no such step exists
(the job has `pull-requests: write` permission but nothing in the file uses it to post a
comment). Removed that claim. A `terraform test` step (`iac/agent/tests/*.tftest.hcl`) was
also added to the workflow on 2026-08-09 (PR #52) — now reflected above.

## What happens after someone merges a PR

`.github/workflows/terraform-apply.yml`, triggered on push to `main` touching `agent/**`, `mcp/**`, `iac/agent/**`. Real step names, in order:

1. **Detect changed paths** — decides which conditional steps below actually run.
2. **Package agent** — `bash scripts/package_agent.sh`, builds the reproducible `agent.tar.gz` (source is embedded inline in Terraform, no GCS staging).
3. **Pin MCP tool spec to currently-live content** — before any apply, so the Agent Registry's tool spec never briefly describes tools a not-yet-deployed revision doesn't support (see `iac/agent/agent_registry_mcp.tf`'s own header comment on the health-gated ordering).
4. **Init** — `terraform init` against the GCS state backend, via Workload Identity Federation (no long-lived keys).
5. **Read currently-deployed MCP image** — so the first apply doesn't accidentally revert the live Cloud Run image.
6. **Apply (ensure infra + Artifact Registry repo exist)** — full `terraform apply`, using the pinned/current spec and image from steps 3/5. Also updates the gateway binding natively (see below) — an ordinary in-place update, no separate attach step.
7. **Build & push MCP image** — conditional, only when the change touches `mcp/**` and `ENABLE_CUSTOM_MCP` is true.
8. **Apply (point Cloud Run at the new MCP image)** — conditional, second apply pointing the service at the just-pushed image.
9. **Verify MCP Cloud Run revision is healthy** — conditional, a real `gcloud run services describe` health check (not an HTTP curl — the service's `INGRESS_TRAFFIC_ALL` setting means it's reachable, but IAM would 401/403 an unauthenticated curl anyway).
10. **Regenerate MCP tool spec now that the new revision is verified healthy** — conditional, only after step 9 passes.
11. **Apply (register verified custom MCP tool spec)** — conditional, the only apply where the tool-spec content can actually change.
12. **Smoke test — verify the agent responds through the gateway** — live invocation of the deployed agent (`imagepull` scenario), asserts a well-formed RCA came back. Real end-to-end proof, not just "the apply succeeded" — this is what would have caught the original mTLS/gateway-binding incident in CI instead of requiring manual investigation.

A failure at any non-conditional step fails the whole job — there's no `continue-on-error` masking a broken deploy.

**No separate "attach gateway" step exists** — since 2026-08-10 (PR #93), the gateway binding (`agentGatewayConfig`) is a native attribute of `google_vertex_ai_reasoning_engine.sre_agent` (`iac/agent/agent_engine.tf`), applied as an ordinary in-place update by step 6/8/11's own `terraform apply`. `scripts/attach_gateway_to_engine.sh` still exists, unchanged, as a manual emergency rollback tool only — CI does not invoke it.

## Approval gate

`environment: production` is set on the apply job, which *can* enforce a manual approval gate if configured in GitHub repo settings — **whether that's actually configured is not visible from the workflow YAML itself**; verify directly in GitHub → Settings → Environments if you need to know whether merges to main deploy unattended or require a click-through.

## What tests run before deployment

- `terraform validate` + `terraform test` (native `.tftest.hcl` tests, e.g. `iac/agent/tests/clusters_json.tftest.hcl`) + `terraform plan` (PR-time, via `terraform-plan.yml`).
- The live smoke test (post-apply, in `terraform-apply.yml` — this is a *post*-deployment gate, meaning a bad deploy has already gone live by the time this runs, but it does catch it and fail the job).
- **The golden evaluation suite is NOT run automatically in CI today.** Running it (`python -m agent.eval.run_eval --mode local` or `--mode remote`) is a manual step — see [Evaluation](../architecture/evaluation.md). This is a real gap: a prompt/model change could pass CI and still regress eval pass rate without anyone noticing until a human runs the suite by hand.

## Artifact / build process

Two artifacts: the agent source archive (`agent.tar.gz`, built reproducibly and embedded directly in the Terraform PATCH), and — conditionally — the custom MCP's container image (pushed to Artifact Registry, tagged with the git SHA).

---

**Related pages:** [Rollback](rollback.md) · [Evaluation](../architecture/evaluation.md) · [Deployment Failure runbook](../runbooks/deployment-failure.md)
