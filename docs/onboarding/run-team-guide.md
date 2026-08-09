# New Run-Team Onboarding Guide

> **Owner:** SRE Agent platform team.
> **Last Verified:** 2026-08-09 — expanded from the original 5-day plan to 10 days, so Day 3
> (MCP/Gateway), Day 4 (cluster routing), Day 5 (IAM/security), and Day 8 (evaluation) each get
> real hands-on time instead of a glossary skim. Every lab below uses commands and files that
> exist in this repo today — none are hypothetical.

A 10-day path for a new engineer with normal cloud/SRE background and zero AI-agent experience.
Each day builds on the last — don't skip ahead. Two reference pages sit alongside this guide and
are used throughout, not duplicated here:

- **[Code Reference Map](code-reference-map.md)** — "where in the code does X actually happen?"
  One row per capability: main code, function, config, Terraform, tests, and a runtime-proof
  command you can actually run.
- **[Code Ownership Map](code-ownership-map.md)** — "where does the code live, who owns
  troubleshooting it, where do its logs land?" Same capabilities, ownership/operational lens.

When a day below says "find X using the Code Reference Map," that means: open the map, find the
row, don't ask anyone.

---

## Day 1 — Architecture and end-to-end flow

- Read [System Overview](../architecture/system-overview.md) first, all the way through.
- Read [Glossary](glossary.md) alongside it — don't skip terms you don't recognize.
- Read [LangGraph Workflow](../architecture/langgraph-workflow.md) and [Investigation
  Loop](../architecture/investigation-loop.md) — understand the node-by-node flow and exactly
  what stops the loop.
- **Lab**: draw the graph flow from memory (pen and paper is fine), then check it against the
  [LangGraph Workflow](../architecture/langgraph-workflow.md) diagram. Note what you got wrong.

## Day 2 — LangGraph and state

- Read [Context and State](../architecture/context-and-state.md), [Evidence
  Architecture](../architecture/evidence-architecture.md), [Confidence
  Scoring](../architecture/confidence.md), [RCA Generation](../architecture/rca-generation.md).
- **Lab**: run a real investigation —
  ```bash
  python3 invoke_agent.py --scenario imagepull
  ```
  Then, using only the `run_id` from the output, manually reconstruct the full picture using
  [Logs](../operations/logging.md)'s query sequence — find the `sre-agent-investigations` entry,
  the evidence records, the confidence breakdown. Don't just read the final RCA text; find the
  raw evidence behind each claim in it.
- **Checkpoint**: open the [Code Reference Map](code-reference-map.md) row "How is RCA
  generated?" and confirm you can name `rca_builder()`'s two key sub-steps
  (`_derive_status()`, `_write_observability_log()`) without re-reading the row.

## Day 3 — MCP and Agent Gateway

> This day was thin in the original 5-day plan (glossary links only). It now includes a
> hands-on exercise that touches both real MCP sources, not just terminology.

- Read [MCP Architecture](../architecture/mcp-architecture.md), [Dynamic MCP
  Routing](../architecture/dynamic-mcp-routing.md), and [Agent Gateway](../architecture/agent-gateway.md).
  Pay attention to the Agent Gateway page's flagged discrepancy: a Terraform comment describes
  the IAP extension as `DRY_RUN`, but the live `terraform.tfvars` sets it to **ENFORCE** — this
  is the kind of doc-vs-code gap you should learn to distrust comments about and verify yourself.
- Read the [Code Reference Map](code-reference-map.md) rows "How does the agent select an
  MCP?" and "Where are MCP calls made?" — note that MCP source selection (`mcp_router()` Phase 1,
  `agent/nodes/mcp_router.py`) is a **deterministic** decision keyed off the cluster registry's
  `type` field, not an LLM guess; only the specific **tool** within that MCP (Phase 2) is
  LLM-selected.
- **Lab — read the tool allowlists directly**:
  ```bash
  grep -n "^GKE_REMOTE_TOOLS\|^CUSTOM_K8S_TOOLS" agent/mcp_client.py
  ```
  Open both frozensets (`agent/mcp_client.py:30` and `:40`). Confirm for yourself that every
  name starts with `list_`, `get_`, or `describe_` — this is the read-only guarantee stated in
  [Least-Privilege IAM](../least-privilege-iam.md), and you're about to see it enforced in Day 5.
- **Lab — trace both MCP sources for the same run**: rerun Day 2's investigation but pick a
  scenario, then find its `mcp_source` field in the `sre-agent-investigations` Cloud Logging
  entry (per the [Code Reference Map](code-reference-map.md)'s "MCP routing" row). Confirm it
  says `gke_remote_mcp` — this is the only MCP source that is actually reachable live today.
  Then open `iac/agent/cloudrun_mcp.tf` and `mcp/server.py` and confirm for yourself why the
  custom MCP (`k8s_mcp`) is code-complete but not reachable: `enable_custom_mcp` defaults
  `false`, and per the [Master Status Matrix](../management/implemented-vs-planned-matrix.md)
  no Load Balancer/Serverless NEG exists anywhere in `iac/` for it. This is "built, not
  deployed" — be precise about that distinction when you explain it to someone else.
- **Lab — prove the read-only claim for the custom MCP path too**:
  ```bash
  cd mcp && python -m pytest tests/test_no_mutation.py -v
  ```
  This is the live-passing test (2/2) that blocks any future mutating call pattern from
  slipping into the 27-tool `CUSTOM_K8S_TOOLS` surface — the same enforcement layer Day 5 will
  use for the live GKE Remote MCP path.

## Day 4 — Cluster routing and multi-cluster architecture

> This day was thin in the original 5-day plan (one paragraph inside Day 4's failure labs). It
> now covers the 5-tier chain, the 2026-08-09 multi-cluster fix, and a real hands-on exercise.

- Read [Cluster Routing](../architecture/cluster-routing.md) in full — the 5-tier priority
  chain inside `resolve_cluster_routing()` (`agent/mcp_client.py:648-777`): exact_id →
  verified_alert_metadata → approved_alias → project_env_namespace → unresolved safe-stop. Each
  tier stops at the first one that resolves to exactly **one** cluster; the agent never guesses.
- Read the [Code Reference Map](code-reference-map.md) row "How do we add a cluster?" — the
  registry is Terraform-rendered (`iac/agent/variables.tf`'s `additional_clusters`,
  `iac/agent/main.tf`'s `clusters_json = jsonencode(...)`), never hand-edited in GCS (it's
  silently overwritten on the next `apply`).
- Read about the 2026-08-09 fix (PR #52): before this fix, adding a second cluster to the
  registry silently wiped the first. This is fixed — the registry now correctly merges
  `local.default_cluster` with `var.additional_clusters` — but understand *why* the old
  behavior was dangerous: a config change that looked correct in the Terraform diff could
  silently drop live cluster routing for an existing cluster.
- **Lab — run the real Terraform test suite that proves the fix**:
  ```bash
  cd iac/agent
  terraform init -backend=false -input=false
  terraform test
  ```
  This runs `iac/agent/tests/clusters_json.tftest.hcl` against the isolated
  `iac/agent/tests/testdata/clusters_json` mirror module — no real cloud resources, no GCP
  credentials needed. Confirm you get 4/4 passing: the default single-cluster case (backward
  compatibility), the 2-synthetic-cluster case, and 2 collision-guard cases (an
  `additional_clusters` entry that collides with the default cluster's name — exact match and
  whitespace-padded match — both must fail the plan, not silently merge).
- **Lab — add a synthetic cluster yourself, using the real test pattern**: open
  `iac/agent/tests/clusters_json.tftest.hcl` and find the `run
  "two_additional_synthetic_clusters_render_correctly"` block (~line 60). Copy its
  `additional_clusters` variable shape and add a **third** synthetic cluster (e.g.
  `synthetic-cluster-4`, any project/region/type you like) to a new `run` block, or extend the
  existing one. Re-run `terraform test` and confirm your new cluster renders in
  `output.clusters_json` with the fields you set. **Do not** point this at a live cluster or run
  it against real state — this is deliberately a config/rendering-layer test with no cloud
  credentials involved; that's the whole point of the isolated `testdata/clusters_json` mirror
  module (see the file's own header comment for why it's separate from the real `iac/agent`
  module).
- **Checkpoint**: explain in your own words why `resolve_cluster_routing()`
  (`tests/test_multi_cluster_registry.py`) and `clusters_json.tftest.hcl` are testing two
  *different* layers (Python routing logic vs. Terraform rendering) — both had to pass before
  the multi-cluster fix was considered done (13/13 and 4/4, per the [Master Status
  Matrix](../management/implemented-vs-planned-matrix.md)).

## Day 5 — Agent Identity, IAM and security

> This day did not exist in the original 5-day plan. It's added because IAM/security review is
> the highest review bar in this repo's own change-management tiering (two-reviewer minimum,
> security-aware reviewer) — a Run-team member needs to be able to reason about it, not just
> trust that it's fine.

- Read [Agent Identity](../architecture/agent-identity.md) — the agent runs as an
  `AGENT_IDENTITY` (`iac/agent/agent_engine.tf:98`), not a service account with static
  credentials. Confirm for yourself:
  ```bash
  grep -rn google_service_account_key iac/
  ```
  Expect zero matches. This is the actual proof behind "no static credentials," not a claim to
  take on faith.
- Read **[Least-Privilege IAM](../least-privilege-iam.md)** in full — every identity in this
  project (runtime Agent Identity in Project A, cross-project Agent Identity in Project B, the
  CI/CD deployer service account, Google-managed platform service agents, the optional custom
  MCP runtime SA) and exactly which roles it holds and why. Note the cross-project set for GKE
  access is exactly four read-only roles (`container.viewer`, `mcp.toolUser`, `logging.viewer`,
  `monitoring.viewer`) — no write access, no broad project roles.
- **Lab — the read-only-proof walkthrough**. This capability is proven at three independent
  layers, per the [Master Status Matrix](../management/implemented-vs-planned-matrix.md) — walk
  all three yourself, don't just read that it's true:
  1. **IAM layer** — the real predefined role has zero mutating permissions:
     ```bash
     gcloud iam roles describe roles/container.viewer
     ```
     Confirm the `includedPermissions` list contains no `create`/`patch`/`delete`/`update` verbs.
  2. **Tool-naming layer** — every tool name in both allowlists is read-only by construction
     (Day 3's lab already showed you this: `grep -n "^GKE_REMOTE_TOOLS\|^CUSTOM_K8S_TOOLS"
     agent/mcp_client.py`, all 33 tools are `list_*`/`get_*`/`describe_*`).
  3. **Test layer** — the live-passing regression guard:
     ```bash
     cd mcp && python -m pytest tests/test_no_mutation.py -v
     ```
     Confirm 2/2 pass. This is what would actually catch a future tool addition that slips a
     write verb past review.
- **Lab**: cross-check `docs/least-privilege-iam.md` against `iac/agent/iam.tf` and
  `iac/gke-access/crossproject_iam.tf` yourself for one role of your choosing — pick any row in
  the audit table, find the matching Terraform resource, and confirm the role string and scope
  match. This is the same manual cross-check Step 6 of the [promotion
  process](../promotion/01-test-to-work-process.md) requires before any IAM-touching change is
  promoted.
- **Checkpoint**: explain why `roles/container.viewer` for GKE access is resource-level/
  role-scoped rather than a broad `roles/editor` grant, and name the specific enforcement layer
  that would catch a regression if someone tried to add a mutating tool later.

## Day 6 — Evidence, confidence and RCA

- Read [Evidence Architecture](../architecture/evidence-architecture.md), [Confidence
  Scoring](../architecture/confidence.md) again (this time focused on the two genuinely separate
  scoring functions — `score_investigation_completeness()` and `score_root_cause_confidence()`,
  disjoint weight sets, never blended before `derive_outcome()`), and [RCA
  Generation](../architecture/rca-generation.md).
- **Lab**: using the [Code Reference Map](code-reference-map.md)'s "How is evidence stored?"
  and "How is confidence calculated?" rows, find the exact `ev_entry` field names
  (`agent/nodes/evidence_extractor.py:131-143`) and confirm — don't assume — which field holds
  the tool name (`source`) vs. the MCP source (`mcp_source`). This exact swap was found and
  fixed in the documentation on 2026-08-09; verify against the live code, not memory of a doc.
  ```bash
  gsutil ls gs://<evidence_bucket>/<run_id>/
  ```
  using a `run_id` from one of your earlier labs.
- **Lab**: run
  ```bash
  pytest tests/test_scorer.py -v
  ```
  and read a couple of the completeness/root-cause test cases to see concretely what inputs
  push confidence up or down.
- **Checkpoint**: state, in one sentence each, the difference between Investigation Completeness
  and Root Cause Confidence, and why `POLICY_VERSION = "1.0.0-uncalibrated"`
  (`agent/confidence/policy.py:20`) means neither number should be read as a calibrated
  probability yet — you'll need this distinction again on Day 8.

## Day 7 — Observability and troubleshooting

- Read [Observability](../operations/observability.md), [Logging](../operations/logging.md),
  [Tracing](../operations/tracing.md), [Alerting](../operations/alerting.md).
- **Lab**: pick a `run_id` from an earlier lab, find its `trace_id`, open it in Cloud Trace, and
  identify which node took the longest. Cross-reference with `node_token_usage` log events for
  the same run.
- **Lab**: run through the full [Daily Health Check](../operations/daily-health-check.md)
  checklist for real, against the live system.
- **Lab (safe, reversible)**: deliberately trigger a few real failure modes and confirm you can
  diagnose them using the [Troubleshooting Runbooks](../runbooks/) directory, without needing to
  ask anyone:
  - Send a payload with no `query` field — confirm you can find the resulting safe-stop in the
    logs and explain why it happened, referencing [Context and State](../architecture/context-and-state.md).
  - Send a payload with a nonsense/unregistered cluster name — confirm you can trace it through
    the 5-tier routing chain from Day 4 to `unresolved` and find the corresponding alert
    (`unresolved_cluster` log-based metric).
  - Review — don't trigger, just read — the [Gateway Failure](../runbooks/gateway-failure.md)
    and [MCP Failure](../runbooks/mcp-failure.md) runbooks, and identify which failure modes are
    currently *real, live-possible* risks (per [Risks and Limitations](../management/risks-and-limitations.md))
    vs. which describe infrastructure that isn't deployed at all (e.g., the custom MCP failure
    alert — same "built, not deployed" distinction from Day 3).

## Day 8 — Evaluation and accuracy

> This day did not exist in the original 5-day plan. It's added because confidence (Day 6) and
> accuracy are genuinely different things, and a Run-team member needs to be able to run the
> real eval suite, not just read about it.

- Read [Evaluation and AI Quality](../architecture/evaluation.md) in full, including the
  2026-08-09 section describing the two real eval-harness bugs that were found and fixed that
  day (stale expected tool names in `golden_cases.py`; a `recursion_limit` mismatch between
  local eval mode and the deployed agent) — both are now guarded by dedicated regression tests
  (`tests/test_golden_cases_tool_names.py`, `tests/test_recursion_limit_consistency.py`).
- **Lab — run the golden-case eval suite locally**, per its own documented command:
  ```bash
  source scripts/init-env.sh
  python -m agent.eval.run_eval --mode local
  ```
  This runs all 14 golden cases (`agent/eval/golden_cases.py`) through `score_case()`
  (`agent/eval/run_eval.py:73-133`), scoring trajectory recall, keyword accuracy, confidence,
  and outcome match.
- **Lab**: compare your run's headline numbers against the current reference baseline,
  [`docs/baselines/tool-scaling-baseline-2026-08-09-corrected.md`](../baselines/tool-scaling-baseline-2026-08-09-corrected.md)
  — 14/14 cases completing execution, 12/14 trajectory recall ≥ 0.5, 100% correct MCP source
  selection, 0 failed tool calls. If your numbers differ materially, that's a real signal to
  investigate, not noise to ignore.
- **Lab**: run the two regression guards directly and read what each one actually checks:
  ```bash
  pytest tests/test_golden_cases_tool_names.py tests/test_recursion_limit_consistency.py -v
  ```
- **Checkpoint — confidence vs. accuracy, stated precisely**: confidence (Day 6) is the agent's
  own self-assessment of how complete its investigation was and how sure it is of the root
  cause — computed from the investigation's own inputs, with no ground truth involved.
  Accuracy/eval score (today) is measured **against a labeled reference** (the golden cases'
  `expected_trajectory` and expected outcome) — it's an external check on whether the agent
  actually did the right thing, not the agent's opinion of itself. The two can diverge: nothing
  in this repo currently measures whether a *high-confidence* RCA is actually *more likely to be
  correct* — that's exactly what "confidence calibration measurement" being labeled ❌ in the
  [Master Status Matrix](../management/implemented-vs-planned-matrix.md) means. Also note there
  is no LLM-as-judge / rubric grading anywhere in this codebase — evaluation today is
  deterministic trajectory/keyword scoring only.

## Day 9 — Terraform and deployment

- Read [Updating the Agent / CI/CD](../operations/deployment.md), [Terraform / Infrastructure
  Management](../operations/terraform.md), [Rollback](../operations/rollback.md).
- **Lab**: make a trivial, reversible change (e.g., a log message tweak in a node), open a PR,
  watch `terraform-plan.yml` run — including its `terraform test` step (added 2026-08-09, PR
  #52, running `iac/agent/tests/clusters_json.tftest.hcl` on every PR touching
  `iac/agent/**`) — get it reviewed, merge, and watch `terraform-apply.yml` run end-to-end:
  package agent → apply #1 → conditional MCP image build → conditional apply #2 → register
  Agent Registry → attach gateway (the self-healing gateway-attach step) → live smoke test. Then
  read [Rollback](../operations/rollback.md) and (in a genuinely safe dev context, not live
  production) practice the rollback procedure. Note it's documented but, per the [Master Status
  Matrix](../management/implemented-vs-planned-matrix.md), has never actually been executed —
  your practice run is real, useful evidence, not a formality.
- **Checkpoint**: using the [Code Ownership Map](code-ownership-map.md)'s "Deployment / CI-CD"
  row, name every step in `terraform-apply.yml`'s pipeline in order, without looking.

## Day 10 — Hands-on incident investigation and change promotion

- Run a full, real incident investigation end-to-end on your own, choosing a scenario you
  haven't used in an earlier day's lab:
  ```bash
  python3 invoke_agent.py --scenario crashloop --verbose
  ```
  Trace it completely: cluster routing tier (Day 4) → MCP source and tool calls (Day 3) →
  evidence records (Day 6) → confidence scores (Day 6) → final RCA (Day 2) → structured log
  entry and trace (Day 7). Do this without opening any of the earlier days' notes — this is the
  integration test for everything so far.
- Read **[Test-Repo → Work-Repo Promotion Process](../promotion/01-test-to-work-process.md)**
  in full — this is how a validated change in this personal test repo
  (`sre-agent-gateway`/`sreagent-t2-demo`) gets reproduced (never copied) into the company work
  repository: 8 validation steps here (pytest, `terraform test`, `mcp/tests/test_no_mutation.py`,
  a real GKE test via `invoke_agent.py`, the eval suite vs. baseline, IAM review, doc updates,
  migration manifest) before anything touches the company repo, then 13 more steps there
  (branch, reproduce, replace config, `terraform fmt`/`validate`/`test`/`plan`, application
  tests, peer review, non-prod deploy, smoke test, eval comparison, approval, prod deploy,
  post-deploy verification, rollback if needed).
- **Lab**: read the "Values that must NEVER be copied verbatim" table at the end of that page
  (project IDs, cluster name, notification email, `github_repo`, tfstate bucket, region, subnet
  CIDR) and explain, for each one, in your own words, what would actually break — or worse,
  silently succeed against the wrong target — if it were copied as-is into the company repo.
- **Lab**: using the same page's Step 8, sketch (doesn't need to be filed) what a migration
  manifest would look like for the trivial change you made and merged on Day 9, using the real
  template at [`docs/promotion/02-migration-manifest-template.md`](../promotion/02-migration-manifest-template.md).

---

## Before you're considered fully onboarded

You should be able to, without help:

- **Explain architecture** — walk through the end-to-end flow (Day 1) and the difference
  between `AgentState`, evidence, and long-term memory (Day 2/6), citing real files, not just
  concepts.
- **Trace one investigation** — take a `run_id` and reconstruct its full picture: cluster
  routing decision, MCP source and tools called, evidence records, confidence scores, and final
  RCA (Day 2, Day 10).
- **Find implementation code** — use the [Code Reference Map](code-reference-map.md) to answer
  "where does X actually happen?" for any capability in this system, without guessing or asking.
- **Identify the selected cluster** — for a given run, name which of the 5 routing tiers
  resolved it, or confirm it safe-stopped to `unresolved` (Day 4).
- **Identify the selected MCP** — for a given run, name whether `gke_remote_mcp` or `k8s_mcp`
  was selected and why (Day 3).
- **Identify tool calls** — for a given run, list which specific tools were called and confirm
  each one is in the correct MCP's allowlist (Day 3).
- **Understand confidence** — explain the difference between Investigation Completeness and Root
  Cause Confidence, and why the policy is explicitly labeled uncalibrated (Day 6).
- **Distinguish confidence from accuracy** — explain why a high-confidence RCA is not the same
  claim as a *correct* RCA, and that nothing in this repo currently measures the correlation
  between the two (Day 8).
- **Troubleshoot failures** — diagnose the intentional failure scenarios from Day 7 using only
  the runbooks, and know which documented failure modes are real/live vs. built-but-not-deployed
  (Day 3, Day 7).
- **Add a cluster** — explain the Terraform-only path (`additional_clusters` →
  `terraform apply`), run `terraform test` against `clusters_json.tftest.hcl`, and know why
  hand-editing `clusters.json` in GCS is wrong (Day 4).
- **Deploy safely** — walk through the CI/CD pipeline step by step and have practiced the
  rollback procedure at least once (Day 9).
- **Promote changes between repositories** — explain the "reproduce, don't copy" principle and
  name at least 3 values from the "must never be copied verbatim" table and why each one is
  dangerous to copy (Day 10).

Also, separately: name at least 3 things this system's own documentation says are *not* working
today, and why that matters (see [Risks and Limitations](../management/risks-and-limitations.md))
— a good Run-team member knows the gaps as well as the working parts.

---

**Related pages:** [Code Reference Map](code-reference-map.md) · [Code Ownership
Map](code-ownership-map.md) · [Glossary](glossary.md) · [System
Overview](../architecture/system-overview.md) · [Troubleshooting Runbooks](../runbooks/) ·
[Test-Repo → Work-Repo Promotion Process](../promotion/01-test-to-work-process.md) ·
[Implemented vs Planned Matrix](../management/implemented-vs-planned-matrix.md)
