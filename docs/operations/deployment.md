# Updating the Agent / CI/CD

> **Implementation Status:** IMPLEMENTED
> **Last Verified:** 2026-08-08 — `.github/workflows/terraform-apply.yml`, `.github/workflows/terraform-plan.yml`
> **Source of Truth:** `.github/workflows/terraform-apply.yml`
> **Owner:** SRE Agent platform team.

## The full deployment path

```
developer change (agent/, mcp/, or iac/agent/)
  → branch, PR
  → terraform-plan.yml runs on the PR (validate + plan, posts as a PR comment)
  → merge to main
  → terraform-apply.yml runs
```

## What happens after someone merges a PR

`.github/workflows/terraform-apply.yml`, triggered on push to `main` touching `agent/**`, `mcp/**`, `iac/agent/**`. Steps, in order:

1. Checkout.
2. **Package agent**: `bash scripts/package_agent.sh` — builds the reproducible `agent.tar.gz` (required since source is embedded inline in Terraform, no GCS staging).
3. Auth via Workload Identity Federation (no long-lived keys).
4. `terraform init` against the GCS state backend.
5. **Apply #1**: full `terraform apply -auto-approve`, ensures infra + Artifact Registry repo exist (runs before any image build, since the repo doesn't exist until first enabled).
6. **Build & push MCP image** — conditional on `ENABLE_CUSTOM_MCP` repo variable AND the change touching `mcp/**`.
7. **Apply #2** — conditional, points Cloud Run at the newly-pushed image.
8. **Verify MCP Cloud Run revision is healthy** — conditional, a real `gcloud run services describe` health check (not an HTTP curl, since the ingress setting means an external curl can't reach it anyway).
9. **Register Agent Registry endpoints** — Google API endpoints always; the custom MCP's tool-spec, conditionally.
10. **Attach gateway to engine** — self-heals the one known recurring flake (`code: 3, "The Reasoning Engine failed to be updated"`) by recreating the engine and retrying once; any other error fails the job loudly.
11. **Smoke test**: live invocation of the deployed agent (`imagepull` scenario), asserts a well-formed RCA came back.

A failure at any non-conditional step fails the whole job — there's no `continue-on-error` masking a broken deploy.

## Approval gate

`environment: production` is set on the apply job, which *can* enforce a manual approval gate if configured in GitHub repo settings — **whether that's actually configured is not visible from the workflow YAML itself**; verify directly in GitHub → Settings → Environments if you need to know whether merges to main deploy unattended or require a click-through.

## What tests run before deployment

- `terraform validate` + `terraform plan` (PR-time, via `terraform-plan.yml`).
- The live smoke test (post-apply, in `terraform-apply.yml` — this is a *post*-deployment gate, meaning a bad deploy has already gone live by the time this runs, but it does catch it and fail the job).
- **The golden evaluation suite is NOT run automatically in CI today.** Running it (`python -m agent.eval.run_eval --mode local` or `--mode remote`) is a manual step — see [Evaluation](../architecture/evaluation.md). This is a real gap: a prompt/model change could pass CI and still regress eval pass rate without anyone noticing until a human runs the suite by hand.

## Artifact / build process

Two artifacts: the agent source archive (`agent.tar.gz`, built reproducibly and embedded directly in the Terraform PATCH), and — conditionally — the custom MCP's container image (pushed to Artifact Registry, tagged with the git SHA).

---

**Related pages:** [Rollback](rollback.md) · [Evaluation](../architecture/evaluation.md) · [Deployment Failure runbook](../runbooks/deployment-failure.md)
