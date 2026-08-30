# Test-Repo → Work-Repo Promotion Process

> **Implementation Status:** Process document, not enforced by tooling. The gates that
> already exist as real automation (`terraform-plan.yml`, `terraform-apply.yml`, `terraform
> test`, `pytest`, `mcp/tests/test_no_mutation.py`) are called out explicitly below as
> **Automated**. Everything else (peer review, security sign-off, manual comparison against
> the baseline) is a **Manual** step this document defines — nothing in this repo or the
> company repo currently blocks a merge on them.
> **Last Verified:** 2026-08-09.
> **Owner:** Ashmin (personal test repo `sre-agent-gateway`, GCP project `sreagent-t2-demo`)
> promoting validated changes into the company work repository.

## Why this document exists

This repo (`sre-agent-gateway`, personal GCP project `sreagent-t2-demo`) is where every
change to the SRE agent gets built and proven first — real Terraform applies, real GKE
cluster calls, real eval runs. The company repository is a **different Terraform state,
different GCP projects, different GKE cluster, different CI secrets** — copying files
across would carry this repo's identifiers (project IDs, cluster name, bucket names) into
an environment where they are wrong, and would skip every verification step that makes a
change trustworthy in the first place.

**"Reproduce, don't copy"** means: re-apply the same code change (same diff to
`agent/*.py`, same Terraform resource/variable pattern in `iac/agent/*.tf`) against the
company repo's own `terraform.tfvars`, own CI secrets, and own cluster — verified there
independently, not assumed to work because it worked here.

## Process overview

```
┌─ IN THIS TEST REPO (sre-agent-gateway, sreagent-t2-demo) ─────────────────┐
│  1. Unit tests (pytest)                                                    │
│  2. Terraform tests (terraform test)                                       │
│  3. Integration tests (mcp/tests/test_no_mutation.py + related)            │
│  4. Real GKE test (invoke_agent.py against the real sre-test-cluster)      │
│  5. Evaluation suite (agent/eval/run_eval.py, compare vs baseline)         │
│  6. Security / IAM review                                                  │
│  7. Documentation update                                                   │
│  8. Migration manifest generation                                          │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─ IN THE COMPANY REPO ──────────────────────────────────────────────────────┐
│  9.  Create company-repo branch                                            │
│  10. Reproduce the change (not copy) using the manifest                    │
│  11. Replace test-specific config with company values                      │
│  12. terraform fmt / validate / test / plan (company repo, company state)  │
│  13. Application tests in the company repo                                 │
│  14. Peer review (per docs/governance/change-management.md tiering)        │
│  15. Deploy to company non-prod                                            │
│  16. Smoke test (company non-prod)                                         │
│  17. RCA / eval comparison against this repo's baseline                    │
│  18. Approval                                                              │
│  19. Production deploy (if appropriate)                                    │
│  20. Post-deploy verification                                              │
│  21. Rollback (only if needed)                                             │
└──────────────────────────────────────────────────────────────────────────┘
```

Steps 1-8 happen entirely in this repo, before anything touches the company repo. Steps
9-21 happen in the company repo and never in this one.

---

## Part 1 — Validate in this test repo

### Step 1 — Unit tests (pytest)

```bash
pytest
```

`pyproject.toml`'s `[tool.pytest.ini_options]` sets `testpaths = ["tests"]`, so this
picks up every file under `tests/` (14 files today, e.g. `tests/test_context_resolver.py`,
`tests/test_mcp_router.py`, `tests/test_rca_builder_integration.py`,
`tests/test_multi_cluster_registry.py`). Per the [Implemented vs Planned
Matrix](../management/implemented-vs-planned-matrix.md), only 3 of the 9 LangGraph nodes
(`context_resolver`, `mcp_router`, `rca_builder`) have direct test coverage today — a
change to one of the other 6 nodes (`input_normalizer`, `task_planner`, `tool_executor`,
`evidence_extractor`, `task_evaluator`, `loop_controller`) passes `pytest` cleanly without
proving the node itself was exercised. Be honest about this gap in the migration manifest
(Step 8) rather than treating a green `pytest` run as full coverage.

**Gate:** all tests pass, `0 failed`. **Automated** in CI via `terraform-plan.yml`'s
scope is Terraform-only — `pytest` itself is not currently a CI step in this repo's
workflows (confirmed: `.github/workflows/*.yml` runs `terraform validate`/`terraform
test`/`terraform plan`, no `pytest` invocation found). Run it manually before promoting.

### Step 2 — Terraform tests (`terraform test`)

```bash
cd iac/agent
terraform init -backend=false -input=false
terraform test
```

This runs `iac/agent/tests/clusters_json.tftest.hcl` — 4 checks against the isolated
`tests/testdata/clusters_json` mirror module (no real cloud resources, no GCP credentials
needed): default single-cluster output matches legacy behavior, 2 synthetic additional
clusters render correctly, and 2 collision-guard cases (exact-name and
whitespace-padded-name) correctly fail the plan. **Automated** — this is a real CI step in
`terraform-plan.yml` (added 2026-08-09, PR #52), runs on every PR touching `iac/agent/**`.

If the change touches `iac/agent/main.tf`'s `clusters.json` rendering or
`variables.tf`'s `additional_clusters` validation, extend
`clusters_json.tftest.hcl` with a new `run` block before promoting — don't promote a
Terraform behavior change with no test proving it.

### Step 3 — Integration tests

```bash
cd mcp && python -m pytest tests/test_no_mutation.py -v
```

`mcp/tests/test_no_mutation.py` is the live-passing integration test that proves the
custom MCP tool surface (`CUSTOM_K8S_TOOLS`, 27 tools) contains no mutating call pattern —
this is the enforcement layer behind the "read-only" claim in
[Least-Privilege IAM](../least-privilege-iam.md). If the change adds or renames a tool in
`mcp/tools/`, this must still pass 2/2 (its current live-verified result) before
promotion — a new tool that slips a write verb past this test is exactly the failure mode
it exists to catch.

Also re-run the cross-layer routing check if the change touches cluster registry
resolution:

```bash
pytest tests/test_multi_cluster_registry.py tests/test_mcp_router.py -v
```

This is the Python-side counterpart to Step 2's Terraform test — it proves
`resolve_cluster_routing()` (`agent/mcp_client.py`, ~line 648-777) picks the correct
cluster from a *rendered* registry, not just that the registry renders correctly in
isolation.

**Gate:** both suites pass with the exact counts recorded in the migration manifest (Step
8) — e.g. "2/2", "13/13" — not just "passed."

### Step 4 — Real GKE test (`invoke_agent.py`)

```bash
source scripts/init-env.sh
python invoke_agent.py --scenario crashloop --verbose
python invoke_agent.py   # full scenario set, if the change is broad
```

This calls the **deployed** Reasoning Engine in `sreagent-t2-demo` against a real fixture
on the real `sre-test-cluster` (e.g. the `crashloop-pod` scenario) and asserts a
well-formed RCA comes back — the same shape of check `scripts/smoke_test.sh` runs in CI's
`terraform-apply.yml`. This is the step that proves the change works end-to-end against a
live GKE cluster and a live Agent Gateway, not just in a unit-test mock.

Run this only after the change is actually deployed to this repo's own `sreagent-t2-demo`
project (via `make tf-agent-apply` or a merged PR through `terraform-apply.yml`) — it
tests the live engine, not local code.

**Gate:** the RCA returned is well-formed (has `likely_root_cause`, non-empty evidence,
a confidence value) and matches the expected finding for that fixture. Record the
scenario(s) run and pass/fail in the migration manifest.

### Step 5 — Evaluation suite (`agent/eval/run_eval.py`)

```bash
source scripts/init-env.sh
export CLUSTER_CONFIG_BUCKET=$(terraform -chdir=iac/agent output -raw cluster_config_bucket_name)
export EVAL_BUCKET=$(terraform -chdir=iac/agent output -raw eval_bucket_name)
python3 -m agent.eval.run_eval --mode local --cases all --output docs/baselines/tool-scaling-baseline-<date>.json
```

Runs all 14 golden cases (`agent/eval/golden_cases.py`) through `score_case()`
(`agent/eval/run_eval.py:73-133`), scoring trajectory recall, keyword accuracy,
confidence, and outcome match. **Compare the new run's headline numbers against the
existing reference baseline**,
[`docs/baselines/tool-scaling-baseline-2026-08-09-corrected.md`](../baselines/tool-scaling-baseline-2026-08-09-corrected.md):

| Metric | Reference baseline (2026-08-09-corrected) |
|---|---|
| Cases completing execution (no crash) | 14/14 |
| Trajectory recall ≥ 0.5 | 12/14 |
| Correct MCP source selection | 100% |
| Failed tool calls | 0 |

A change that drops trajectory recall or introduces a failed tool call is a regression —
investigate before promoting, don't wave it through because "it still deploys." A change
that has no plausible effect on agent reasoning (e.g. a CI workflow comment, a docs-only
change) can skip a full re-run, but say so explicitly in the manifest rather than silently
omitting the eval step.

**Gate:** no regression on trajectory recall, MCP source selection accuracy, or failed
tool call count, relative to the reference baseline above.

### Step 6 — Security / IAM review

Not a script — a manual read against the actual IAM diff. Steps:

1. `terraform plan` and read every `google_project_iam_member` /
   `google_storage_bucket_iam_member` / similar resource in the diff.
2. Cross-check against [Least-Privilege IAM](../least-privilege-iam.md)'s existing
   inventory — does the new grant match an existing pattern (resource-level, narrowest
   predefined role), or does it introduce something broader (`roles/editor`,
   project-level where resource-level would do)?
3. Apply the review tier from
   [Change Management](../governance/change-management.md): an IAM/permission change
   needs a **two-reviewer minimum, security-aware reviewer** — the highest bar in that
   table, above even a new cluster or new MCP server.
4. If the change adds a new MCP tool, confirm it is read-only (`list_*`/`get_*`/
   `describe_*` verb) and that Step 3's `mcp/tests/test_no_mutation.py` run actually
   covered it.

**Gate:** no IAM grant broader than what [Least-Privilege IAM](../least-privilege-iam.md)
already documents as the pattern, without an explicit, recorded justification.

### Step 7 — Documentation update

Update the relevant page(s) under `docs/` in *this* repo to reflect the change —
following the standard this knowledge base already holds itself to (see
[Documentation Validation Report](../../archive/SUPERSEDED_2026-08-20_DOCUMENTATION-VALIDATION-REPORT.md)): every
substantive claim needs a `file:line` citation, and if the change affects a status label,
update [Implemented vs Planned](../management/implemented-vs-planned-matrix.md) in the
same change, not as a follow-up. Do not promote a code change to the company repo while
this repo's own docs still describe the old behavior — that recreates exactly the kind of
doc-drift the 2026-08-09 re-verification pass had to go back and fix.

### Step 8 — Migration manifest generation

Generate the migration manifest using the template at
[`docs/promotion/02-migration-manifest-template.md`](02-migration-manifest-template.md) —
record the diff summary, the exact test/eval results from Steps 1-6 (pass counts, not just
"passed"), the IAM diff, and the doc pages updated. This manifest is the artifact that
travels with the change into the company repo's PR description — it's how a reviewer there
verifies the change was actually validated here, without re-deriving it from scratch.

---

## Part 2 — Reproduce in the company repository

### Step 9 — Company-repo branch creation

In the company repo (not this one): create a feature branch off the company repo's `main`,
following that repo's own naming convention. Never branch off or merge from this test
repo's git history — the two repos share no commit ancestry and should not be force-merged
together.

### Step 10 — Reproduce the change (not copy)

Concretely, for this repo's conventions, "reproduce" means:

- **Python code changes** (`agent/*.py`, `agent/nodes/*.py`, `agent/confidence/*.py`,
  `mcp/tools/*.py`): re-apply the same logical diff to the company repo's copy of these
  files. If the company repo's copy has diverged (its own prior customizations), merge the
  *behavior*, not a blind file overwrite.
- **Terraform changes** (`iac/agent/*.tf`): re-apply the same **resource/variable
  pattern** — e.g. if this repo added a new `variable` block to `variables.tf` with a
  `validation` block (as `additional_clusters` did), add the equivalently-named variable
  with the same validation logic to the company repo's `variables.tf`, then wire it into
  the company repo's own `main.tf` the same way — do not copy `iac/agent/main.tf` wholesale,
  since the company repo's `main.tf` will already differ (different backend, different
  module composition).
- **CI workflow changes** (`.github/workflows/*.yml`): re-apply the same step logic (e.g.
  the `terraform test` step, or the self-healing retry block in
  `terraform-apply.yml`), adapted to the company repo's actual secret names and any
  additional steps its workflow already has that this repo doesn't.
- **Test changes** (`tests/*.py`, `iac/agent/tests/*.tftest.hcl`): copy these more
  literally — a test's job is to assert the same behavior in both places, so unlike
  application code, the test *should* look close to identical. Still update any
  hardcoded values that reference this repo's identifiers (e.g. `sreagent-demo` used as
  a synthetic project ID inside `clusters_json.tftest.hcl`'s `variables` block is a test
  fixture, not a real project — it can carry over as-is; a real project ID must not).

### Step 11 — Replace test-specific configuration

See the **Values That Must Never Be Copied Verbatim** section below — this is the step
where every one of those values gets swapped for the company repo's own.

### Step 12 — `terraform fmt` / `validate` / `test` / `plan` (company repo)

Run the same four checks this repo's own `Makefile` and CI define, against the company
repo's own state and credentials:

```bash
terraform fmt -recursive
terraform init                       # company repo's own backend config
terraform validate
terraform test                       # if the company repo has an equivalent tftest.hcl
terraform plan
```

Read the plan output line by line — the same discipline as Step 6, applied here against
the company repo's real project/cluster. A plan that shows changes to resources you didn't
intend to touch (e.g. a full `google_vertex_ai_reasoning_engine` recreation from a
`gemini_model` variable drift, the exact failure mode this repo's own CI comments warn
about) is a stop condition, not something to `apply` through.

### Step 13 — Application tests in the company repo

Re-run the equivalent of Steps 1-3 (pytest, `terraform test`, integration tests) against
the company repo's own copy of the code, with the company repo's own test cluster/fixtures
where applicable. A test passing in this repo does not prove it passes in the company
repo's environment — different cluster, different IAM, potentially different tool
versions.

### Step 14 — Peer review

Apply [Change Management](../governance/change-management.md)'s review-tiering table in
the company repo's PR:

| Change type touched | Review bar |
|---|---|
| IAM/permission grant | Two-reviewer minimum, security-aware reviewer |
| New cluster / new MCP server / new MCP tool | Platform team + security review before enabling |
| Model or prompt change | Platform team + run the golden eval suite before promoting |
| Graph change (node add/remove/reorder) | Platform team, architectural review |
| Confidence logic change | Platform team + eval-dataset owner |
| Network/security (gateway, Model Armor) | Security review mandatory |

None of these tiers are technically enforced by branch protection in this test repo today
— attach the migration manifest to the company PR so the reviewer has the evidence from
Part 1 in front of them, and confirm the company repo's own review process (which may have
stricter enforcement) is followed on top of this table.

### Step 15 — Deploy to company non-prod

Apply via the company repo's own CI/CD path (its equivalent of
`terraform-apply.yml`) or its own controlled manual apply process, targeting its
non-production environment first — never production directly from an unreviewed branch.

### Step 16 — Smoke test (company non-prod)

Run the company repo's equivalent of `scripts/smoke_test.sh` / `invoke_agent.py` against
its own non-prod deployment — same principle as Step 4, applied to the new environment.
A clean `terraform apply` does not prove the gateway binding or the live GKE connection
actually works (this repo's own history includes a case where the bind reported
`done=true` while the runtime connection was still broken — see
[Documentation Validation Report](../../archive/SUPERSEDED_2026-08-20_DOCUMENTATION-VALIDATION-REPORT.md)).

### Step 17 — RCA / eval comparison against this repo's baseline

Run the company repo's equivalent of Step 5 (`agent/eval/run_eval.py` or its company-repo
counterpart) against its own non-prod deployment, and compare the results against **this
repo's reference baseline**,
[`docs/baselines/tool-scaling-baseline-2026-08-09-corrected.md`](../baselines/tool-scaling-baseline-2026-08-09-corrected.md).
The company repo will have its own cluster and its own fixtures, so exact numeric parity
isn't the bar — but a materially worse trajectory recall, a new failed tool call, or a new
crash relative to this baseline's headline numbers (14/14 completing execution, 12/14
trajectory recall ≥ 0.5, 100% correct MCP source selection, 0 failed tool calls) is a
signal something was reproduced incorrectly, not just environment variance.

### Step 18 — Approval

Explicit sign-off from whoever owns the company repo's production environment, informed by
Steps 14-17's results. This is a human gate, not automated — record who approved and when
in the company repo's PR.

### Step 19 — Production deploy (if appropriate)

Only after Step 18's explicit approval, and only if the change is actually intended for
production (some changes may be validated in non-prod only, e.g. an experimental prompt
change). Use the company repo's own production deploy path, with the same manual-approval
gate this repo's own `terraform-apply.yml` reserves via its `environment: production`
GitHub Actions environment.

### Step 20 — Post-deploy verification

Re-run Step 16's smoke test against the production deployment. Confirm structured logs and
metrics are flowing (the company repo's equivalent of this repo's [Observability
Operations](../operations/observability.md) checks) before considering the promotion
complete.

### Step 21 — Rollback (only if needed)

If Step 20 fails, or a regression surfaces after deploy: use the company repo's Terraform
state to `apply` the prior commit's configuration (`terraform apply` against the last-known
good revision), the same pattern this repo's own rollback procedure describes (flagged in
[Implemented vs Planned](../management/implemented-vs-planned-matrix.md) as "real,
documented procedure exists; no evidence it has ever actually been executed" — so treat a
first real rollback as a chance to verify the procedure itself, not just the fix). Do not
leave a partially-applied Terraform state — finish the rollback `apply` fully before
declaring the incident resolved.

---

## Values that must NEVER be copied verbatim

Every one of these comes from `iac/agent/terraform.tfvars.example` (or the CI workflow
files that reference the same variables) and is specific to this personal test repo /
`sreagent-t2-demo`. Copying any of them into the company repo would point company
infrastructure at this personal project, or silently overwrite this repo's own state.

| Variable | This repo's example value | Why it must not be copied | Replace with |
|---|---|---|---|
| `project_a_id` | `"my-agent-project"` (example) / real value is `sreagent-t2-demo` | Points Terraform's `google` provider at this personal GCP project — applying company changes here would modify personal infrastructure, not company infrastructure | The company's own agent-hosting GCP project ID |
| `project_b_id` | `"my-gke-project"` (example) | Points cross-project IAM and GKE access at this repo's personal GKE-hosting project | The company's own GKE-hosting GCP project ID |
| `gke_cluster_name` | `"sre-test-cluster"` | Names this repo's specific test cluster — a company deployment pointed at a cluster named `sre-test-cluster` in the company's own project either fails (doesn't exist) or, worse, silently succeeds against an unrelated cluster that happens to share the name | The company's real target GKE cluster name |
| `notification_email` | `"sre-oncall@example.com"` | Cloud Monitoring alerts would route to a placeholder/personal address instead of the company's on-call channel | The company's real on-call/alerting distribution address |
| `github_repo` | `"my-org/testing2-gcp-sre-agent"` | This is the exact repo string allowed to assume the deployer identity via Workload Identity Federation (`iac/agent/oidc.tf`) — leaving this repo's value in place would let *this* GitHub repo's Actions deploy into the *company's* GCP project | `owner/name` of the company's actual repo, matching where its CI actually runs |
| `tfstate_bucket` | not in the example file (supplied via CLI `-var`/CI secret `TFSTATE_BUCKET`) — this repo's real bucket is project-specific and gitignored | Terraform state is the single source of truth for what's deployed; pointing the company repo's `terraform init -backend-config="bucket=..."` at this repo's state bucket would let a company `apply` read/write this personal repo's state, corrupting both | The company's own GCS state bucket, provisioned and access-controlled separately per environment |
| `region` | `"us-central1"` | Not dangerous by itself, but silently inheriting the example instead of an explicit company decision can violate the company's own data-residency/region policy | Whatever region the company's cloud/compliance policy requires |
| `agent_subnet_cidr` / PSC-interface subnet | `"10.0.0.0/24"` (example) | Could collide with the company's existing VPC CIDR ranges, causing a real network conflict on apply | A CIDR block chosen against the company's actual VPC address plan, confirmed non-overlapping |

**How this template is meant to be used, per its own header comment**: "Copy this file to
`terraform.tfvars` and fill in your values. `terraform.tfvars` is gitignored." The template
itself already enforces "don't commit real values" for this repo — the same discipline
applies one level up: don't let this repo's *filled-in* values (even though gitignored
locally) leak into the company repo's own `terraform.tfvars` by copy-paste. Every value in
the table above should come from a deliberate decision made *for the company's own
environment*, not from what happened to be sitting in this repo's tfvars file.

---

**Related pages:** [Migration Manifest Template](02-migration-manifest-template.md) ·
[Change Management](../governance/change-management.md) ·
[Least-Privilege IAM](../least-privilege-iam.md) ·
[Implemented vs Planned Matrix](../management/implemented-vs-planned-matrix.md) ·
[Tool-Scaling Baseline (reference)](../baselines/tool-scaling-baseline-2026-08-09-corrected.md)
