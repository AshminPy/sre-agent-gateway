# Migration Manifest Template

> **Purpose:** the artifact produced at Step 8 of the [Test-Repo → Work-Repo Promotion
> Process](01-test-to-work-process.md) — it travels with a validated change into the
> company repo's PR description, so a reviewer there can see what was proven in this repo
> (`sre-agent-gateway`, personal GCP project `sreagent-t2-demo`) without re-deriving it from
> scratch. One manifest per change being promoted.
> **Last Verified:** 2026-08-09.
> **Owner:** Ashmin.

## How to use this file

1. Copy everything under **"Blank template — copy from here"** into a new file (or the
   company PR description directly).
2. Fill in every section with real values from Steps 1-7 of the promotion process — pass
   counts, not "passed"; real file paths, not "various files"; the actual IAM diff, not
   "no IAM changes" unless `terraform plan` actually showed none.
3. Leave a section explicitly empty (e.g. write "None" under IAM CHANGES) rather than
   deleting it — a reviewer should see that every category was checked, not guess whether
   it was skipped or genuinely empty.
4. A worked example, built from this repo's real PR #52 (2026-08-09), follows the blank
   template — use it as the calibration for how much detail each section expects.

---

## Blank template — copy from here

```markdown
# CHANGE (name of feature/change)

# WHY (why it was needed)

# ARCHITECTURE IMPACT (what architectural behavior changes)

# TEST REPOSITORY CHANGES (files changed)

# APPLICATION CHANGES (functions/classes changed)

# TERRAFORM CHANGES (files/resources/variables changed)

# CONFIGURATION CHANGES (JSON/YAML/env/etc.)

# IAM CHANGES (new/removed permissions)

# MCP CHANGES (Gateway/server/tool changes)

# NEW TESTS (tests added)

# OBSERVABILITY CHANGES (logs/metrics/traces)

# COMPANY REPOSITORY ACTION (exactly what must be reproduced)

# VARIABLES THAT MUST NOT BE COPIED (test project IDs, clusters, buckets, regions, etc.)

# VERIFICATION (commands/tests)

# ROLLBACK (exact safe rollback)

# MANAGEMENT SUMMARY (2-3 sentences, plain English)
```

---

## Worked example — PR #52, multi-cluster `clusters.json` fix (2026-08-09)

This is a real manifest for a real merged change in this repo: commit `7316aa3`
(`fix(agent): support multiple clusters in clusters.json via Terraform (#52)`), merged
2026-08-09. Every fact below was re-confirmed directly against `git show 7316aa3` and the
files it touched while writing this template — not reconstructed from memory.

```markdown
# CHANGE

Multi-cluster support for `clusters.json` — the runtime cluster registry the agent reads
from GCS at investigation time. Previously the Terraform template
(`iac/agent/clusters.json.tftpl`) hardcoded exactly one cluster object; this replaces it
with a `map(object(...))` variable merged with the always-present default cluster and
rendered via `jsonencode(...)`.

# WHY

`clusters.json` only ever supported one cluster. Any manually-added second cluster entry
in the GCS bucket was silently wiped on the next `terraform apply`, because the bucket
object has no `lifecycle { ignore_changes }` block and the template had no loop construct.
This blocked onboarding a second GKE cluster durably — flagged as a known operational
limitation in `docs/architecture/cluster-routing.md` before the fix, with a documented
workaround (re-upload after every apply) that was explicitly "fragile, not recommended."

# ARCHITECTURE IMPACT

- `clusters.json` generation moves from `templatefile(...)` over a static `.tftpl` file to
  `jsonencode(...)` over a Terraform-native `merge()` of two maps
  (`iac/agent/main.tf:39-69`: `local.default_cluster`, `local.all_clusters`,
  `local.clusters_json`).
- Terraform is now explicitly documented as the **sole source of truth** for this file —
  hand-editing it in GCS was never supported, but the code comments previously told
  operators to do exactly that (`agent/mcp_client.py`, two locations) and have been
  corrected to point at `var.additional_clusters` instead.
- No change to `resolve_cluster_routing()` (`agent/mcp_client.py`, ~line 648-777) or the
  5-tier priority chain itself — this fix is entirely upstream, in how the registry file
  gets built, not in how it gets read.
- New behavior: a name collision between `var.gke_cluster_name` and an
  `var.additional_clusters` key now **fails `terraform plan`** (a real `validation`
  block, `iac/agent/variables.tf:41-67`) instead of silently letting `merge()` override the
  default entry.

# TEST REPOSITORY CHANGES (files changed)

16 files changed, 956 insertions, 37 deletions (`git show 7316aa3 --stat`):

- `iac/agent/variables.tf` — new `variable "additional_clusters"` block (+38 lines)
- `iac/agent/main.tf` — rewritten `local.clusters_json` rendering (+46/-9 lines)
- `iac/agent/versions.tf` — `required_version` bumped `>= 1.6.0` → `>= 1.9.0`
- `iac/agent/clusters.json.tftpl` — deleted (15 lines removed, no longer used)
- `iac/agent/tests/clusters_json.tftest.hcl` — new, 175 lines, 4 `run` blocks
- `iac/agent/tests/testdata/clusters_json/main.tf` + `versions.tf` — new isolated test-fixture module (100 + 3 lines)
- `tests/test_multi_cluster_registry.py` — new, 196 lines, 8 test functions
- `agent/mcp_client.py` — 2 comment/error-message corrections (+9/-3 lines), no logic change
- `.github/workflows/terraform-plan.yml` — new `terraform test` CI step (+8 lines)
- `docs/architecture/cluster-routing.md`, `docs/runbooks/add-gke-cluster.md`,
  `docs/management/risks-and-limitations.md`, `NEXTSTEPS.md` — doc updates reflecting the fix
- `docs/baselines/tool-scaling-baseline-2026-08-09.md` +
  `tool-scaling-baseline-2026-08-09-raw.json` — a separate, smaller task folded into the
  same PR (tool-scaling baseline capture); unrelated to the cluster-registry fix itself,
  called out here for completeness since it's part of the same diff.

# APPLICATION CHANGES (functions/classes changed)

- `agent/mcp_client.py` — no function signature or logic change. Two comment/error-message
  corrections only: the module-level comment above `MCP_REGISTRY` (~line 147-150) and the
  `ValueError` message in the cluster-not-found path (~line 626-633) both previously told
  operators to hand-edit `clusters.json` in GCS — both now point at
  `var.additional_clusters` in Terraform instead, matching the new source-of-truth model.
- No other `agent/*.py` function was touched by this change.

# TERRAFORM CHANGES (files/resources/variables changed)

- **New variable**: `additional_clusters` (`iac/agent/variables.tf:41-67`) —
  `map(object({ aliases, project, region, type, environment, allowed_namespaces, owner,
  enabled }))`, default `{}` (empty — existing single-cluster deployments unaffected).
  Carries a `validation` block: fails plan if any key (after `trimspace()`) collides with
  `var.gke_cluster_name`.
- **Changed local values** (`iac/agent/main.tf:39-69`): `local.default_cluster` (the
  existing single cluster, now expressed as a one-entry map), `local.all_clusters`
  (`merge(local.default_cluster, var.additional_clusters)`), `local.clusters_json`
  (rewritten from `templatefile(...)` to `jsonencode({ clusters = [for name, c in
  local.all_clusters : {...}] })`).
- **Deleted file**: `iac/agent/clusters.json.tftpl` — no longer referenced anywhere.
- **`required_version` bump**: `iac/agent/versions.tf`, `>= 1.6.0` → `>= 1.9.0` — needed
  because the cross-variable `validation` block (referencing `var.gke_cluster_name` from
  inside `var.additional_clusters`'s own validation) requires Terraform 1.9+. CI already
  pinned exactly `1.9.0`, so this tightens nothing CI wasn't already using — confirmed no
  CI breakage.
- A `check` block (Terraform's other validation construct) was tried first for the
  collision guard and rejected in review: `check` blocks only ever emit a warning, they
  never fail `plan`/`apply` — proven with a real `terraform plan` exit-code comparison
  before switching to a `validation` block. Recorded here so this isn't silently
  re-attempted in the company repo.

# CONFIGURATION CHANGES (JSON/YAML/env/etc.)

- **Runtime config**: `clusters.json`'s JSON *shape* is unchanged (same fields per entry:
  `name`, `aliases`, `project`, `region`, `type`, `environment`, `allowed_namespaces`,
  `owner`, `enabled`) — only how it's generated changed. Verified live: applying to
  `sreagent-t2-demo` produced a field-for-field identical file for the existing
  single-cluster case.
- **New `terraform.tfvars` key** (optional, defaults to none set): `additional_clusters`
  — see the runbook example in `docs/runbooks/add-gke-cluster.md` for the exact HCL shape.
- **CI workflow config**: `.github/workflows/terraform-plan.yml` gained one new step
  (`terraform test`), no new secrets or env vars required — the test module under test has
  no providers, so it runs against whatever credentials the job already has.

# IAM CHANGES (new/removed permissions)

**None.** No `google_project_iam_member`, `google_storage_bucket_iam_member`, or any other
IAM resource appears anywhere in `git show 7316aa3`'s diff — confirmed, `iam.tf` and
`iac/gke-access/*` are not in the changed-files list. This change only affects how
`clusters.json` content is generated, not who can read/write it.

**Known, explicitly out-of-scope gap** (reported per the commit's own note, not fixed
here): adding a cluster in a **different** GCP project than the existing one gets a
`clusters.json` entry via `var.additional_clusters`, but **no IAM grant** —
`iac/gke-access/providers.tf` is hardwired to a single scalar `project_b_id`, not a list.
That cluster will `403` at runtime until `iac/gke-access` is separately extended and
applied against the new project. This is documented in
[Implemented vs Planned Matrix](../management/implemented-vs-planned-matrix.md) as
"Cross-project IAM for additional clusters — 🔵 DESIGNED/PLANNED."

# MCP CHANGES (Gateway/server/tool changes)

No Gateway, MCP server, or tool-surface change. `GKE_REMOTE_TOOLS` (6 tools) and
`CUSTOM_K8S_TOOLS` (27 tools) in `agent/mcp_client.py` are untouched — this PR changes
*which clusters* the registry can list, not *which tools* are available against them.

# NEW TESTS (tests added)

- `iac/agent/tests/clusters_json.tftest.hcl` — new, Terraform-native, 4 `run` blocks
  against an isolated `tests/testdata/clusters_json` mirror module (no real cloud
  resources, no GCP credentials needed):
  1. `default_only_matches_legacy_single_cluster_output` — proves the fix is backward
     compatible for existing single-cluster deployments.
  2. `two_additional_synthetic_clusters_render_correctly`
  3. `colliding_cluster_name_fails_the_check`
  4. `whitespace_padded_colliding_name_still_fails` — the whitespace-trim edge case
     specifically.
  Wired into CI (`.github/workflows/terraform-plan.yml`) — runs on every PR touching
  `iac/agent/**`, not just locally.
- `tests/test_multi_cluster_registry.py` — new, 8 test functions, Python-side, mocked GCS.
  Proves `resolve_cluster_routing()` (`agent/mcp_client.py`) picks the correct cluster from
  a *rendered* multi-cluster registry — the cross-layer counterpart to the Terraform test
  above (that one proves the registry renders correctly in isolation; this one proves the
  agent reads a rendered registry correctly).
- Real connectivity test (not a repo test file, a live run): `invoke_agent.py --scenario
  imagepull` against the real `sre-test-cluster`, run after applying this change live to
  `sreagent-t2-demo` (see NEXTSTEPS.md's 2026-08-09 status entry).

# OBSERVABILITY CHANGES (logs/metrics/traces)

None. No logger, metric, or trace instrumentation was added, removed, or modified by this
change — `iac/agent/monitoring.tf` is not in the diff.

# COMPANY REPOSITORY ACTION (exactly what must be reproduced)

Per [Step 10](01-test-to-work-process.md#step-10--reproduce-the-change-not-copy) of the
promotion process — reproduce the pattern, do not copy the files wholesale:

1. **Terraform**: add the equivalent `variable "additional_clusters"` block (same object
   type, same `optional()` defaults, same collision-guard `validation` block referencing
   the company repo's own default-cluster variable name) to the company repo's own
   `variables.tf`. Rewrite the company repo's own `local.clusters_json` (wherever it
   currently lives — its `main.tf` will already differ in backend/module composition) to
   use the same `default_cluster` / `merge()` / `jsonencode()` pattern — do not copy
   `iac/agent/main.tf` wholesale.
2. Bump the company repo's own `required_version` to `>= 1.9.0` if it's currently lower
   — confirm the company CI's pinned Terraform version supports it first (this repo's CI
   already pinned exactly `1.9.0`, so this bump was free here; verify that isn't a company
   CI blocker before applying it there).
3. Delete the company repo's equivalent hardcoded single-cluster `.tftpl` file, if one
   exists, once the `jsonencode()` path is in place and tested.
4. **Python**: apply the same two comment/error-message corrections in the company repo's
   copy of `mcp_client.py` (if it has diverged, only the *behavior* — pointing operators at
   Terraform instead of GCS hand-edits — needs to carry over, not a literal text match).
5. **Tests**: copy `clusters_json.tftest.hcl` and `test_multi_cluster_registry.py`
   near-literally (per Step 10's guidance, tests should look close to identical across
   repos) — but replace the test-fixture project ID (see below) and confirm the company
   repo's default cluster/variable naming matches before the test module compiles.
6. **CI**: add the same `terraform test` step to the company repo's own PR-validation
   workflow, adapted to its actual workflow file name and job structure.
7. **Docs**: update the company repo's own cluster-routing and add-cluster runbook pages,
   the same way `docs/architecture/cluster-routing.md` and
   `docs/runbooks/add-gke-cluster.md` were updated here.
8. Explicitly carry forward the **known gap** (cross-project IAM) as a documented
   limitation in the company repo too — do not let it silently disappear on reproduction.

# VARIABLES THAT MUST NOT BE COPIED (test project IDs, clusters, buckets, regions, etc.)

Per the [Values that must NEVER be copied verbatim](01-test-to-work-process.md#values-that-must-never-be-copied-verbatim)
table in the process doc, applied to this specific change:

| Value in this repo | Where it appears in this change | Must be replaced with |
|---|---|---|
| `project_b_id` (real value `sreagent-t2-demo`'s paired GKE project) | Read by `local.default_cluster` in `iac/agent/main.tf:39-46` — the default cluster entry's `project` field | The company's own GKE-hosting GCP project ID |
| `gke_cluster_name` = `"sre-test-cluster"` | `iac/agent/variables.tf:25-29`, becomes the default cluster's map key | The company's real target GKE cluster name |
| `region` = `"us-central1"` | `local.default_cluster`'s `region` field | The company's own region, per its data-residency policy |
| Test-fixture project ID inside `clusters_json.tftest.hcl`'s synthetic `variables` block | This is a synthetic value used only inside the isolated test module (`tests/testdata/clusters_json`), not a real project — it can carry over as-is per Step 10's test-copying guidance; called out here only so it isn't confused with a real value that needs replacing | No replacement needed — it's already a placeholder, e.g. a string like `"other-project"` used purely for test assertions |

# VERIFICATION (commands/tests)

All run in this repo before promotion, exact results recorded here (not "passed"):

```bash
cd iac/agent
terraform init -backend=false -input=false
terraform test
```
Result: **4/4 passed** — confirmed live 2026-08-09 (also confirmed independently in
[Implemented vs Planned Matrix](../management/implemented-vs-planned-matrix.md)'s header).

```bash
pytest tests/test_multi_cluster_registry.py tests/test_mcp_router.py -v
```
Result: **13/13 passed** (8 from `test_multi_cluster_registry.py` + 5 from
`test_mcp_router.py`) — confirmed live 2026-08-09.

```bash
python invoke_agent.py --scenario imagepull --verbose
```
Result: real connectivity test against `sre-test-cluster`, run after applying this change
live to `sreagent-t2-demo` — completed end-to-end, per the merged commit's own verification
note (`git show 7316aa3`).

```bash
terraform plan   # before switching check{} -> validation{} block, and after
```
Result: used to prove `check` blocks never fail plan/apply (only warn) — the actual
evidence behind choosing `validation{}` instead, not an assumption.

**Not run for this specific change** (explicitly, not silently skipped): the full
`agent/eval/run_eval.py` 14-golden-case suite was not re-run as part of *this* PR's own
verification — the golden-case tool-name staleness and recursion-limit issues discovered
during the adjacent baseline-capture task were fixed in a separate follow-up PR (#53,
`docs/baselines/tool-scaling-baseline-2026-08-09-corrected.md`), not in this one.

# ROLLBACK (exact safe rollback)

`git revert 7316aa3` (or the company repo's equivalent commit once reproduced) followed by
`terraform apply` in `iac/agent/` — this regenerates `clusters.json` from the reverted
`templatefile(...)` + restored `clusters.json.tftpl` path, returning to the single
hardcoded cluster. Confirm no `additional_clusters` entries were relied upon in production
before reverting — a revert silently drops any additional cluster back out of the registry,
which is a real behavior change for anyone using it, not just an infra rollback. This
follows the same pattern documented in
[Implemented vs Planned Matrix](../management/implemented-vs-planned-matrix.md)'s note that
this repo's rollback procedure is "real, documented... no evidence it has ever actually
been executed" — treat a first real rollback of this change as a chance to verify the
procedure itself.

# MANAGEMENT SUMMARY (2-3 sentences, plain English)

The agent could only ever talk to one Kubernetes cluster because of a Terraform bug — any
second cluster added by hand got silently deleted the next time infrastructure was
updated. This fix lets Terraform manage any number of clusters properly, with a safety
check that stops a bad configuration from being applied. No new permissions were granted,
and adding a cluster in a different cloud project still needs a separate, already-known
follow-up before it will fully work.
```

---

**Related pages:** [Test-Repo → Work-Repo Promotion Process](01-test-to-work-process.md) ·
[Change Management](../governance/change-management.md) ·
[Implemented vs Planned Matrix](../management/implemented-vs-planned-matrix.md) ·
[Cluster Routing](../architecture/cluster-routing.md) ·
[Adding a New GKE Cluster (runbook)](../runbooks/add-gke-cluster.md)
