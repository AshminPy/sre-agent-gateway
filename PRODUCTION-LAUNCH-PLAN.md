# SRE Agent — Production Launch Plan

> **Personal-repo tracking only.** This file lives in `sre-agent-gateway` for planning/tracking
> purposes and is intentionally **not** ported to `sre-agent-app-infra` (the company repo) — that
> repo tracks its own rollout separately. Only the *logic* of finished work gets ported there
> (per the personal-repo-first workflow), never this planning doc itself.

_Created 2026-07-28. Status is evidence-based: marked ✅/🟡 only where backed by real code/config (file:line). Scanned the working repo (agent/, mcp/, iac/agent/) before writing. Supersedes the ordering in NEXTSTEPS.md for production-launch work; NEXTSTEPS.md is retained for the deeper per-item research._

## 2026-08-20 status correction — read before using any issue row below

The Phase 1 issue table below was written 2026-08-11. **Twelve of its rows point at issues
that have since CLOSED**: #29, #64, #66, #67, #68, #69, #70, #71, #72, #75, #76, #87. They
still read as pending work. Treat any row naming those issues as historical.

Also corrected as of 2026-08-20:

- **Metric/alert counts** — the note further below says "real count is 11 metrics + 11
  alerts". It is now **12 and 12** (`iac/agent/monitoring.tf`).
- **#77** — its row says lint/security scanning is still missing. It is not: `ruff check`
  (blocking), `pip-audit`, `pytest` and a separate `mcp-pytest` job all run in
  `python-tests.yml` (PR #141). #77 stays **open** for a different reason — the required
  post-deployment live canary has not passed yet, blocked by the #103 condition.
  Implementation complete and deployed ≠ Completed.
- **#78** — likewise implemented, merged and **correct**; open only pending the same live
  canary. Its 2026-08-14 Correction section explicitly excludes `eval/**` and
  `iac/gke-access/**` from its scope, with documented reasons. Do not re-derive scope from
  the struck-through original Fix line.
- **#32** — code fix merged and deployed (PR #96); the real `MATCH_FOUND` / `blocked=True`
  path has never been exercised live. Open pending that.
- **#94** — a fix is proposed in PR #157, currently **open and unmerged**. #94 is not fixed.
- **#103** — closed. Resolved 2026-08-15 via native `stream_query()`, live-validated at
  315.3 s past the old ~300 s transport boundary.

Current capability truth lives in
[`docs/management/implemented-vs-planned-matrix.md`](docs/management/implemented-vs-planned-matrix.md);
current gaps in
[`docs/management/risks-and-limitations.md`](docs/management/risks-and-limitations.md);
task status in `docs/management/PROJECT_TRACKER.xlsx`.

## 2026-08-30 — superseded as the operational status source

This file is kept in place because 15+ live code/test comments cite its "Priority N"
numbering as provenance (`grep -rn "PRODUCTION-LAUNCH-PLAN.md" agent/ tests/`) — moving it
would break those citations. But its day-to-day status role has moved:
**for "where are we now," read [`docs/management/CURRENT-STATE.md`](docs/management/CURRENT-STATE.md)
instead.** This file's own content hasn't been updated since 2026-08-20 (1 commit, vs. 11 to
the tracker in the same window) and should be read as historical framing for the Priority-N
numbering, not current status.

## 2026-08-11 update — Phase 1/Phase 2 plan, management requirements (supersedes the 2026-08-09 section below as the current status source)

**Correction from the first draft of this section (same day):** that draft classified #92 as
a Model Armor-related deferral. **Wrong — verified against the real issue**: #92 is *"Agent
has no pod-log-read permission on the target GKE cluster — logs are unavailable for every
incident."* That's a core investigation-capability gap (the agent can't read pod logs at
all), not a Model Armor item, and it's a real Phase 1 blocker — an SRE agent that can't read
logs can't accurately investigate most real incidents. The actual Model Armor issues are
**#30** (endpoint hostname mismatch) and **#32** (output-sanitization verdict discarded).
Corrected throughout below.

Reframed around **Phase 1 (production MVP)** vs **Phase 2 (post-MVP)**, tracked by GitHub
issue number rather than the Priority-1-12 scheme below. The Priority write-ups stay as
historical detail — this section is the current plan.

### Project objective

Phase 1 delivers a production-ready, **read-only** SRE investigation workflow:

```
PagerDuty → authenticated incident invocation → Agent Engine → deterministic cluster
selection → correct MCP selection → GKE or on-prem/non-GKE cluster → evidence collection
→ controlled investigation loop → confidence + completeness scoring → evidence-backed RCA
→ human review
```

Phase 1 must support: existing GKE cluster via GKE Remote MCP · existing on-prem/non-GKE
cluster via Connect Gateway · Custom Kubernetes MCP deployed securely in Google Cloud ·
PagerDuty automatic invocation · accurate confidence scoring · golden evaluation cases ·
complete observability/monitoring · production security controls · complete operational
documentation · human-controlled remediation · no automatic production changes.

### Keep (already real, verified live this session)
- Gemini 2.5 Pro as the deployed model
- The `agent/llm/` adapter design (#63 PR 1 — provider-neutral interface, Gemini-only
  adapter, `LLM_PROFILE`-driven selection)
- Accurate token tracking (input/cached/candidates/reasoning/tool-use/total — verified
  live: 20/20 per-call log entries summed exactly to the final reported totals)
- Run ID, provider, and model telemetry
- Read-only investigation (no write/exec/port-forward anywhere in `mcp/`, enforced by
  `mcp/tests/test_no_mutation.py`)
- Evidence-backed RCA (every claim cites a real evidence ID)
- Human approval required before any remediation (no auto-remediation exists)

### #63 — cost cleanup scope (unchanged from the earlier draft, restated for completeness)
- Keep the `agent/llm/` adapter from PR #100.
- Keep accurate input/cached/candidate/reasoning/tool-use/total token telemetry.
- Remove the estimated dollar cost — it depends on manually maintained Terraform rate
  variables; real spend visibility belongs to Cloud Billing, not a hand-maintained table.
- Do not build dynamic pricing in Phase 1 — that's Phase 2.

**Monitoring decision (Option 1, corrected 2026-08-11):** `iac/agent/monitoring.tf`'s
`google_logging_metric.investigation_cost` + `google_monitoring_alert_policy.cost_spike`
currently fire on `jsonPayload.estimated_cost_usd > 0.10`. Once that field is removed, this
alert would go silently dark (filter never matches again, no error). **Replacing it with a
provider-neutral token-usage warning** instead of just deleting it — same per-investigation
anomaly-detection purpose (Cloud Billing only gives aggregate spend, no per-`run_id`
granularity), keyed on `jsonPayload.tokens_total` (a field `agent/llm/`'s adapter design
already produces for any provider, not a Gemini-specific dollar figure).

**Threshold design — implemented 2026-08-11, monitoring/token-budget slice only:**
`MAX_TOKENS_PER_RUN` turned out to already exist and already be enforced —
`agent/nodes/loop_controller.py` has read it from the environment as a hard per-run token
cap (default `100000`) since before this change; Terraform never set it, so production
always silently ran on that Python-side default. Implemented:
- `max_tokens_per_run` (new Terraform variable, `iac/agent/variables.tf`, default `100000`
  — matches the pre-existing Python fallback exactly, so this alone changes no live
  behavior) is now the single source of truth, passed to Agent Engine as
  `MAX_TOKENS_PER_RUN` (`iac/agent/agent_engine.tf`'s `local.agent_env`).
- `token_warning_ratio` (new variable, default `0.8`, validated to `(0, 1]`).
- A new `google_logging_metric.token_usage` + `google_monitoring_alert_policy.token_usage_warning`
  (`iac/agent/monitoring.tf`) whose `threshold_value = var.max_tokens_per_run *
  var.token_warning_ratio` — computed by Terraform, never hardcoded. Verified live with two
  different variable pairs (`100000 * 0.8 = 80000`, `50000 * 0.6 = 30000`) — both matched
  exactly in a real `terraform plan`.
- Deliberately does **not** touch `google_logging_metric.investigation_cost` /
  `google_monitoring_alert_policy.cost_spike` or `estimated_cost_usd` anywhere — removing
  the dollar-cost fields is a separate, later #63 PR, out of scope for this one.
- Regression tests: `tests/test_loop_controller_token_budget.py` (6 tests — env var
  default/override/invalid-value/disable, under/over-budget exit behavior).

**Correction, 2026-08-12 review:** the first version of `token_usage_warning` had three
real bugs, all fixed before merge: (1) its metric filter (`jsonPayload.tokens_total > 0`)
also matched `agent/nodes/*.py`'s per-node `node_token_usage` events, which carry a
non-zero running-total `tokens_total` too — confirmed with real data (one run produced 3
matching log lines, not 1) — now restricted to `event_type="sre_agent_run"`; (2) the
alert's `resource.type="global"` was wrong — a real `sre_agent_run` log entry's own
`resource` field carries `aiplatform.googleapis.com/ReasoningEngine`, confirmed via a
live `gcloud logging read`, not assumed — alert filter corrected to match; (3)
`max_tokens_per_run=0` (which disables the hard cap in `loop_controller.py`) left the
alert enabled with a meaningless threshold of 0 — now `enabled = var.max_tokens_per_run
> 0`. Also corrected the documentation wording: this is a near-budget operational alert
on COMPLETED investigations, not a real-time warning during the run that triggers it —
`sre_agent_run` is only written after the graph finishes.

**Status:** the monitoring/token-budget slice above is implemented — [PR #102](https://github.com/AshminPy/sre-agent-gateway/pull/102), CI green, not yet merged or deployed. **Only the dollar-cost removal (removing `estimated_cost_usd` and the manually-maintained Terraform pricing variables) remains** — that is a separate, later #63 PR with its own tests and Terraform plan, not started.

### Phase 1 — must fix and validate

**Reliability and core investigation:** #31 (SSE parser can silently return an empty first
frame as success) · #35 (API-enabled/endpoint-registered checks collapse failures into
false) · #63 (cost cleanup, above) · #74 (token/cost/latency counters are process-global,
can leak between investigations sharing a warm container) · #92 (no pod-log-read
permission on the target GKE cluster) · #94 (recurring Terraform drift — must be resolved
or fully explained and controlled) · #95 (reliable service-status/user-impact detection).

**Cluster and routing safety:** #73 (missing-cluster requests must never default to
`sre-test-cluster`) · #85 (custom Cloud Run MCP must become operational for the on-prem
path) · #86 (complete only the parts required for the existing GKE + on-prem clusters) ·
#89 (complete routing-safety E2E test suite). Requirements: GKE routes only to GKE Remote
MCP; on-prem/non-GKE routes only to Custom MCP; selection is deterministic, never
model-chosen; unknown/missing/disabled/conflicting/ambiguous cluster info stops safely,
never defaults; evidence proven to come from the selected cluster, never mixed across
clusters/investigations; routing reason + cluster + namespace + workload + MCP source
always recorded.

**Custom MCP and Connect Gateway:** runs securely in Google Cloud; Cloud Run MCP reaches
the registered non-GKE cluster through Connect Gateway with short-lived auth, no stored
kubeconfig or long-lived credentials; least-privilege IAM + K8s RBAC; every tool stays
read-only (no create/update/patch/delete/exec/port-forward); auth+authz between Agent
Gateway and Custom MCP; every supported tool verified through the deployed cloud path, not
only local testing; Connect-Gateway-unavailable / MCP-unavailable / authz-denied /
wrong-cluster scenarios tested; onboarding, removal, credential flow, failure handling,
rollback documented.

**PagerDuty (#88):** authenticated webhook receiver; validate PD signatures; normalize
fields (incident ID, service, environment, cluster, project, region, namespace, workload
when available); dedup + idempotent invocation; one correlation/run ID preserved
end-to-end; never guess a cluster when routing info is insufficient; read-only
investigation only, RCA draft for human review, no auto-remediation; test invalid
signatures, duplicate events, missing metadata, routing failure, timeout, downstream-agent
failure.

**Confidence and RCA accuracy:** #65 (weak generic-word claim grounding) · #66 (unverified
model-assigned `observed_fact` label) · #67 (resource identity check ignores
namespace/pod) · #68 (time correlation/freshness don't use real timestamps) · #69 (loop
can exit with required evidence still missing) · #70 (agent doesn't act on its own
generated next step) · #91 (failed tool calls wrongly credited as evidence-domain
coverage) · #95 (service-status/user-impact, listed above too). Requirements: Investigation
Completeness stays separate from Root-Cause Confidence; both computed deterministically,
never model-self-assigned; cluster/namespace/workload/resource identity confirmed; real
evidence timestamps used; stale/missing/failed/contradictory evidence penalized; failed
tools never credited as collected evidence; alternative explanations identified; observed
facts kept distinct from inferences; "unknown"/"insufficient evidence" returned rather than
a forced cause; loop bounded by steps/time/tokens and doesn't finish with mandatory
evidence missing unless that unavailability is explicitly reported; every RCA claim cites
its supporting evidence.

**Evaluation and golden cases:** #80 (two evaluation datasets have drifted apart) · #81
(naive keyword-only pass/fail, groundedness never checked against the real evidence
chain) · #82 (no E2E path from entrypoint through scorer — **the E2E test is Phase 1, the
human-feedback learning system within #82 splits to Phase 2**). Golden cases required:
CrashLoopBackOff, ImagePullBackOff, OOMKilled, Pending pod, missing ConfigMap/Secret,
failed rollout, Service selector/endpoint mismatch, a realistic multi-component incident,
GKE-via-Remote-MCP, non-GKE-via-Custom-MCP+Connect-Gateway, missing evidence, conflicting
evidence, stale evidence, failed tool call, unknown cluster, ambiguous cluster,
wrong-cluster prevention, MCP unavailable, Connect Gateway unavailable, PagerDuty-triggered
investigation. Every agent change requires: deterministic assertions, tool-trajectory
validation, evidence-grounding checks, confidence calibration checks, replay tests,
golden-case regression tests, and a judge model only as a secondary check — never the sole
evaluator. All mandatory deterministic tests must pass before claiming production
readiness.

**Observability, monitoring, privacy:** #29 (Cloud Trace endpoint mismatch) · #64
(`google_billing_budget` shown in docs but not provisioned — resolve or correct the docs)
· #75 (duplicate completion events not scoped by `logName` can double-count metrics) · #76
(100% prompt/response capture in tracing without explicit approval/redaction) · #94
(Terraform telemetry/source drift, listed above too). Required telemetry: one run ID +
trace/correlation ID across PagerDuty→Agent Engine→Agent Gateway→MCP→final RCA; PD
incident ID; provider + model; selected cluster/namespace/workload; routing reason;
selected MCP source; tool name/status/latency; tool failures/retries; loop count + exit
reason; evidence count + domains; investigation completeness; root-cause confidence; token
usage per call and per investigation; Agent/Gateway/MCP/Connect-Gateway latency; final
status; evidence-archive success/failure. Required dashboards + **tested** alerts (not
"Terraform created it" — each triggered in a controlled test with a proven notification):
PagerDuty receiver failures, invalid signatures, routing failures, unknown/ambiguous
clusters, GKE MCP failures, Custom MCP failures, Connect Gateway failures, Agent Gateway
failures, Agent Engine invocation failures, repeated tool failures, excessive latency,
loop/token-limit exits, evidence storage failures, high error rate, low investigation
completeness, Terraform drift.

**Security and supply-chain:** #71 (fallback audit trail records the requested tool, not
what executed) · #72 (fallback doesn't trigger on network errors, invalid tool mapping) ·
#77 (CI runs no tests/lint/security scanning on PRs) · #78 (CI path filters skip
tests/scripts/eval/iac/gke-access) · #79 (unpinned Actions, no Python lock file, MCP
container runs as root) · #84 (VPC/NAT provisioned but never attached to Agent Engine) ·
#87 (Terraform self-heal can auto-recreate the production Reasoning Engine unexpectedly).
For #71/#72: **fix and fully test the fallback, or disable it completely for Phase 1** —
never ship a partially-working fallback with an inaccurate audit trail; the full automatic
fallback can stay in Phase 2 if safely disabled now. For #84: confirm whether VPC/NAT is
actually required by the deployed traffic path — attach + test if yes, remove the unused
resource and correct the docs if no; never leave unused security/network infra that implies
protection it doesn't provide. For #87: **already checked this session** — `grep -n
"-replace=\|failed to be updated"` against the current `.github/workflows/terraform-apply.yml`
finds **zero matches**; the specific self-heal mechanism the issue describes (auto
`-replace=` on a magic failure string) no longer exists in the file. Strong evidence this
is already resolved (likely by the #93 gateway-config-binding migration removing the
failure mode that used to trigger it) — but per "close only after acceptance evidence is
attached," this needs one more explicit confirmation pass (a clean `terraform plan`
showing no `-replace` anywhere in the workflow, cited in the closing comment) before
actually closing it, not closed here. Required CI: Python tests, MCP tests, `terraform
fmt`/`validate`/`test`/`plan`, linting, type/static checks where applicable,
dependency+secret scanning, container vulnerability scanning, IaC security scanning,
pinned Actions, locked Python deps, non-root MCP container, path filters covering
`agent/`, `mcp/`, `tests/`, `eval/`, `scripts/`, `iac/agent/`, `iac/gke-access/`, required
checks before merge.

**Model Armor — controlled Phase 1 trial (#30, #32):** do not simply defer without testing
the currently-documented regional integration. One controlled dev/test validation:
review current official Google docs first; compare against the previous failed
implementation; use regional templates in `us-central1`
(`modelarmor.us-central1.rep.googleapis.com`, not the global endpoint); separate
request/response templates where appropriate; `template_metadata.enforcement_type =
INSPECT_ONLY`; Model Armor Cloud Logging enabled; `HIGH` confidence for
prompt-injection/jailbreak during initial tuning; keep the existing IAP `REQUEST_AUTHZ`
policy, add Model Armor as a **separate** `CONTENT_AUTHZ` policy — never replace IAP.

**Permissions:** required IAM differs by ingress vs. egress path and by which service agent
sits in each path — do not guess or hardcode roles here. Official source of truth:
[Agent Gateway + Model Armor integration](https://docs.cloud.google.com/model-armor/model-armor-agent-gateway-integration).
The Phase 1 trial must verify which paths are actually inspected in *our* current agent
setup (see the coverage table below) before any IAM is granted — grant only what that doc
requires for the paths we confirm are real. **Never grant Model Armor roles to Agent
Identity** (this repo's application runtime identity) unless the official docs explicitly
require it there.

Separate branch + tested rollback before touching the gateway; never test in
production first; **no `INSPECT_AND_BLOCK` in Phase 1**. `failOpen` and `INSPECT_ONLY` are
different controls — inspect-only logs findings without blocking, it is not a preventive
control; don't claim complete coverage without real Model Armor logs/spans as proof.
Coverage table required before implementation (client↔agent, agent↔Gemini, GKE Remote MCP,
Custom MCP, PagerDuty payload, final RCA, internal Agent Runtime/gRPC traffic, any
unsupported/bypassed path) plus the test-case list and decision gate — both spelled out in
full under "Model Armor test plan" below, kept in this doc rather than duplicated in the
chat reply.

### Issues needing an explicit scope split (Phase 1 piece + Phase 2 piece)
| Issue | Phase 1 | Phase 2 |
|---|---|---|
| #71, #72 | Fix and prove the fallback, or disable it safely | More advanced automatic failover design, if still wanted |
| #82 | E2E entrypoint→scorer evaluation | Production human-feedback learning loop, continuous dataset ingestion |
| #84 | Attach required networking or remove unused resources + fix docs | Additional private-network expansion beyond current paths |
| #86 | Support the existing GKE + existing on-prem cluster safely | Generalized multi-project/multi-region/large-scale onboarding |
| #87 | Validate PR #93, close with evidence once the second confirmation pass runs | — (fully closes in Phase 1, no Phase 2 remainder) |
| #30, #32 | Controlled regional inspect-only trial | Remainder only if the trial fails, or `INSPECT_AND_BLOCK` is requested later |

### Phase 2 — post-MVP (do not start until every Phase 1 blocker is closed)
- #33 (Agent Registry endpoint Terraform representation + drift reconciliation) — deferred
  provided the Phase 1 deployment scripts stay idempotent, audited, and smoke-tested
- Remaining generalized #86 work beyond the required GKE + on-prem paths
- #82's human-feedback learning system
- #71/#72's advanced automatic MCP fallback, if disabled (not fixed) in Phase 1
- #30/#32's remaining Model Armor integration, if the regional trial fails
- Model Armor `INSPECT_AND_BLOCK` enforcement, after tuning + security approval
- Dynamic per-investigation dollar pricing
- Cloud Billing export cost attribution by investigation/run ID
- Additional LLM provider adapters + true one-variable `LLM_PROFILE` switching (only
  meaningful once a second adapter exists)
- Additional MCP sources (Prometheus, Grafana, Elastic, Cloud Logging, runbooks, knowledge
  bases)
- More clusters/projects/regions; automated PagerDuty updates; human-approved remediation
  execution; automated remediation only after a separate safety review; long-term memory
  from human-approved RCAs; capacity/load optimization; LangGraph→ADK migration only if a
  clear benefit is proven

**Do not build unused Phase 2 abstractions during Phase 1.**

### Phase 1 production-readiness gate
**Functional:** one signed PD event starts exactly one investigation · correct
cluster/MCP selected · GKE via GKE Remote MCP works · on-prem via Custom MCP + Connect
Gateway works · real evidence gathered · loop is bounded · RCA has cause, evidence,
uncertainty, next checks, suggested remediation · remediation stays human-controlled.
**Accuracy:** every material claim evidence-backed · no failed tool call counted as
evidence · no stale/cross-investigation evidence used · unknown causes stay unknown ·
confidence responds correctly to missing/stale/conflicting/failed evidence · all mandatory
golden cases pass · replay tests pass after every material agent change.
**Security:** Agent Identity is the runtime identity · least-privilege IAM/RBAC verified ·
tools stay read-only · PD signatures validated · MCP endpoints not arbitrarily selectable ·
no secrets in prompts/logs/traces/RCA · containers + deps scanned · Model Armor regional
inspect-only trial completed · if Model Armor can't be used, risk + compensating controls
formally accepted.
**Reliability:** no process-global investigation state · no silent default cluster · no
uncontrolled retries · no unattended Reasoning Engine replacement · timeouts/bounded
retries/safe-stop tested · MCP/gateway failure behavior predictable · deployment + rollback
tested · Terraform has no unexplained drift · previous working release can be restored.
**Observability:** end-to-end run/trace correlation works · logs structured + redacted ·
metrics/dashboards show the complete path · critical alerts triggered and tested ·
PD/Agent-Engine/Gateway/MCP/cluster failures distinguishable · ops can determine why an
investigation failed without reading application code.
**Cost:** accurate token usage retained · inaccurate manual dollar estimates removed ·
Cloud Billing provides actual spend visibility · budget/spending alerts exist or are
confirmed centrally managed · investigation step/token/latency limits configured.
**Operations and documentation:** see the doc list below — a new operations engineer must
be able to understand, deploy, monitor, troubleshoot, and roll back the system from the
documentation alone.

### Documentation to create or update
Architecture/data-flow diagram · implemented-vs-planned capability matrix (exists, needs
refresh) · LangGraph workflow/loop docs · LLM adapter docs (new, for `agent/llm/`) ·
Agent Identity/IAM docs · Agent Gateway docs · Model Armor coverage + known limitations ·
GKE Remote MCP docs · Custom MCP docs (exists, needs refresh) · Connect Gateway docs
(exists, needs refresh) · PagerDuty integration docs (new) · cluster/MCP routing docs ·
confidence-scoring docs (exists) · evaluation/golden-case docs · logging/metrics/
dashboards/alerts docs · deployment runbook · rollback runbook · gateway failure runbook ·
MCP failure runbook · Connect Gateway failure runbook · routing failure runbook ·
identity/permission failure runbook · adding/removing GKE clusters (exists) · adding/
removing non-GKE clusters · adding an MCP server · security controls + accepted risks ·
ownership/escalation contacts · Phase 1 limitations · Phase 2 backlog.

### Working method
Review current main, open issues, PR history, CI, and live evidence — never trust stale
docs or old diagrams. Never mark an issue complete from a code comment or an AI summary
alone. Identify issues already fixed by PR #52, #93, #96, #99, #100 (see the table below).
Close issues only after acceptance evidence is attached. Split mixed Phase 1/Phase 2 issues
(table above). Handle one small issue or tightly related group at a time. Create a rollback
point before each infra change. Run regression tests after every change. Run a focused live
test when the change affects deployed behavior. Never auto-merge — stop for review before
every merge. Never start Phase 2 while Phase 1 blockers remain.

### All 36 open issues — classification table
Status column: **✅ verified this session** (real evidence cited) vs **inherited** (from
the 2026-08-09 remediation-batch audit/labels, not independently re-checked this session —
needs re-confirmation before being treated as current fact). Order = recommended execution
order within Phase 1, grouped by dependency, not strict issue-number order.

| Ord | # | Title | Phase | Status | Why this phase | Depends on | Acceptance evidence needed |
|---|---|---|---|---|---|---|---|
| 1 | 92 | Agent has no pod-log-read permission on target GKE cluster | Phase 1 | inherited | Core investigation capability gap — can't accurately investigate without logs | none | Live tool call reads real pod logs on `sre-test-cluster` |
| 2 | 63 | Gemini cost pricing hardcoded to Flash rates while Pro deployed | Phase 1 | ✅ PR #100 merged (adapter+tokens); cost-removal not yet done | Money-accuracy bug already partially fixed; remainder is this session's next PR | none | Diff + tests + `terraform plan` for the cost-removal PR |
| 3 | 74 | Token/cost/latency counters are process-global | Phase 1 | ✅ confirmed real (same root class the #63 2,548-token gap came from) | Cross-investigation data leakage risk on warm containers | none | Two back-to-back live investigations on the same warm instance show no cross-contamination |
| 4 | 94 | Recurring Terraform drift (TELEMETRY env var + source_archive) | Phase 1 | ✅ confirmed real — reproduced in every `terraform plan` run this session (PR #99, #100, LLM_PROFILE wiring) | Repeatedly seen live, never yet root-caused | none | A `terraform plan` immediately after apply shows 0 changes |
| 5 | 73 | Missing-cluster requests silently default to `sre-test-cluster` | Phase 1 | ✅ **real, verified 2026-08-11** — `context_resolver.py` itself safe-stops correctly, but `agent/main.py:399` (`investigate()`) and `agent/main.py:915` (`SREAgent.query()`, the actual production entrypoint) both do `payload.get("cluster", "sre-test-cluster")` — the default is applied to the payload *before* it ever reaches the graph, so context_resolver's safe-stop never sees a missing cluster to begin with. Stays open until an E2E missing-cluster test proves no default cluster is used anywhere in the real invocation path, not just inside context_resolver.py | Safety-critical: never guess a cluster | none | E2E test: a payload with no `cluster` field produces a safe-stop, not an investigation against `sre-test-cluster` |
| 6 | 35 | API-enabled/endpoint-registered checks collapse failures into false | Phase 1 | inherited | Silent failure masking | none | Test: a real disabled API is distinguishable from "not checked" |
| 7 | 31 | SSE parser can return an empty first frame as success | Phase 1 | inherited | Silent false-positive on a real failure path | none | Test: empty first frame is treated as failure |
| 8 | 91 | Failed tool calls credited as evidence-domain coverage | Phase 1 | inherited | Confidence-integrity bug | none | Test: a forced tool failure does not raise completeness score |
| 9 | 65 | Claim grounding awards full credit for one shared generic word | Phase 1 | inherited | Confidence-integrity bug | none | Test: generic-word-only match scores low |
| 10 | 66 | Confidence scorer trusts model's self-assigned `observed_fact` | Phase 1 | inherited | Confidence must be app-computed, not model-self-assigned | none | Test: model-labeled `observed_fact` without grounding is downgraded |
| 11 | 67 | `resource_identity_match` ignores namespace and pod | Phase 1 | inherited | Wrong-resource false-positive risk | none | Test: same cluster, different namespace/pod scores lower |
| 12 | 68 | `time_correlation`/freshness don't use real timestamps | Phase 1 | inherited | Stale evidence not penalized | none | Test: old evidence timestamp lowers the score |
| 13 | 69 | Loop can exit while required evidence domains are missing | Phase 1 | inherited | Premature conclusion risk | 65-68 (same scoring subsystem) | Test: forced-missing-domain run does not exit early |
| 14 | 70 | Agent doesn't act on its own generated next step | Phase 1 | ✅ observed directly in the 2026-08-09 honest-baseline E2E test (agent identified the exact next call needed, never made it) | Real, reproduced behavior, not just a theoretical gap | 69 | Test: agent takes its own suggested next step before concluding |
| 15 | 95 | No reliable service-status/user-impact detection | Phase 1 | ✅ Option A (honest "not determined" text) shipped in PR #96; this issue is the real check | Option A was an explicit interim fix, this is the real one | none | Live run: Impact Assessment reflects a real checked status, not a fallback string |
| 16 | 89 | No E2E routing-safety test suite | Phase 1 | inherited | Needed to prove 5/73/86 together | 5, 73, 86 | Full scenario matrix passes |
| 17 | 86 | Multi-cluster support is single-cluster in disguise | Phase 1 (existing-cluster scope only) | ✅ partially fixed by PR #52 (multi-cluster `clusters.json` via Terraform); remaining gap is cross-project IAM | PR #52 didn't close this — different-GCP-project clusters get no IAM grant | none | Test: a cluster in a second GCP project gets a working IAM grant, or is explicitly rejected safely |
| 18 | 85 | Custom Cloud Run MCP fallback is non-operational as deployed | Phase 1 | inherited (code exists, per earlier priority-4 write-up below — "not wired to reach clusters via Connect Gateway in production") | Needed for the on-prem path to actually work end-to-end | 3 (Connect Gateway, priority write-up below) | Live Cloud Run MCP call reaches a real non-GKE cluster |
| 19 | 88 | No PagerDuty integration exists | Phase 1 | inherited — confirmed zero PD code in repo (per this doc's own Priority 2 write-up) | Explicit Phase 1 requirement (project objective) | 5, 73, 86, 89 (routing must be safe first) | Signed PD webhook starts one real investigation |
| 20 | 80 | Two evaluation datasets have drifted apart | Phase 1 | inherited | Blocks trustworthy golden-case results | none | One consolidated dataset, both old references updated |
| 21 | 81 | Eval uses naive keyword matching, no real groundedness check | Phase 1 | inherited | Eval results currently not trustworthy | 80 | Groundedness check reads the actual evidence chain |
| 22 | 82 | No E2E entrypoint→scorer test; no human-feedback loop | Phase 1 (E2E part only) | inherited | E2E path is Phase 1; feedback loop splits to Phase 2 | 80, 81 | E2E test runs entrypoint through final score |
| 23 | 29 | Cloud Trace endpoint hostname mismatch | Phase 1 | inherited | Observability correctness | none | Traces actually appear in Cloud Trace |
| 24 | 64 | `google_billing_budget` in docs, not in Terraform | Phase 1 | inherited | Docs must match reality, or the resource must exist | none | Either the resource exists, or docs say budgets are managed centrally |
| 25 | 75 | Duplicate completion events can double-count metrics | Phase 1 | inherited | Metric integrity | none | A replayed/duplicate log event doesn't double-count |
| 26 | 76 | 100% prompt/response capture in tracing, no redaction | Phase 1 | inherited | Privacy/compliance risk | none | Sampled or redacted, with an explicit approval decision on file |
| 27 | 71 | MCP fallback audit trail records requested tool, not actual | Phase 1 (fix-or-disable) | inherited | Audit-trail integrity | none | Either fixed + tested, or fallback disabled with evidence it's off |
| 28 | 72 | MCP fallback doesn't trigger on network errors, bad tool mapping | Phase 1 (fix-or-disable) | inherited | Same fallback subsystem as #71 | 71 | Same as #71 |
| 29 | 77 | CI runs no tests/lint/security scanning on PRs | Phase 1 | ✅ partially fixed — `python-tests.yml` added this session (PR #100); lint/security scanning still missing | Real, verified gap remains after the pytest addition | none | CI shows lint + a security-scan check, not just pytest |
| 30 | 78 | CI path filters skip tests/, scripts/, eval/, iac/gke-access/ | Phase 1 | inherited (my own `python-tests.yml` this session filters on `agent/**`+`tests/**` only — same gap class, not yet fully closed) | Real gap, partially addressed, not closed | none | CI triggers on a change to each listed path |
| 31 | 79 | Unpinned Actions, no Python lock file, MCP container runs root | Phase 1 | 🟡 partially addressed — this session's own new/edited workflow steps (`dorny/paths-filter`) are pinned to a full commit SHA; pre-existing steps (`actions/checkout@v4` etc.) and the lock-file/root-container gaps are not | Supply-chain hardening, real gap | none | All actions pinned to SHA, `requirements.txt` has a lock file, MCP container has a non-root user |
| 32 | 84 | VPC/NAT provisioned, never attached to Agent Engine | Phase 1 (decide+resolve) | inherited | Unused security infra implying protection it doesn't provide | none | Either attached+tested, or removed + docs corrected |
| 33 | 87 | Terraform self-heal can auto-recreate the Reasoning Engine | Phase 1 (near-closed) | ✅ checked this session — the exact `-replace=` self-heal code no longer exists in `terraform-apply.yml` | Strong evidence already resolved by #93; needs one more confirmation pass before closing | none | A `terraform plan`/grep pass confirming no `-replace=` path, cited in the closing comment |
| 34 | 30 | Model Armor endpoint hostname mismatch | Phase 1 (controlled trial) | ✅ confirmed real earlier this session (memory: gateway-level CONTENT_AUTHZ has no working Terraform path — API-level rejection, not a config mistake) | Gated behind the regional inspect-only trial plan | none | Trial coverage table + test cases, both below |
| 35 | 32 | Model Armor output-sanitization verdict discarded | Phase 1 (controlled trial) | ✅ fixed in code (PR #96), reopened and deferred to the Model Armor batch — live verdict-handling still unverified | Same trial gate as #30 | 30 | Phase 1 is `INSPECT_ONLY` — there is no block path to exercise. Acceptance is: a real `MATCH_FOUND`/detection verdict is logged during the trial; the code's response-verdict handling path is exercised against that real verdict; no request is actually blocked (inspect-only mode never blocks) |
| 36 | 33 | Agent Registry endpoint registrations have no Terraform representation | **Phase 2** | inherited | Explicitly deferred by management's requirements (drift-reconciliation nice-to-have, not an MVP blocker) | none | N/A for Phase 1 |

### Already resolved / partial / duplicate / stale / needs-split
- **Resolved, should be closed with evidence once re-confirmed:** #87 (see row 33 above —
  one more confirmation pass, then close).
- **Partially resolved, remainder tracked under the same issue:** #63 (adapter+tokens done,
  cost-removal remaining), #86 (multi-cluster registry done via PR #52, cross-project IAM
  remaining), #77 (pytest CI done, lint/security scanning remaining), #79 (this session's
  new pins done, pre-existing unpinned steps + lock file + root container remaining).
- **Duplicate:** none found among the 36 — no two open issues describe the same underlying
  code location and symptom.
- **Stale claim, corrected 2026-08-11 (was flagged "needs re-verification" in the earlier
  draft — now checked directly against the code):** `context_resolver.py`'s own default-to-
  `sre-test-cluster` claim (this doc's Priority 5 write-up below) was already re-confirmed
  false twice before this session — that specific function really doesn't default. **#73
  itself is not stale — it is real, at a different location.** `agent/main.py:399` and
  `agent/main.py:915` (`SREAgent.query()`, the production entrypoint) both default a
  missing `cluster` field to `sre-test-cluster` in the payload, before `context_resolver.py`
  ever runs — so its safe-stop logic never gets the chance to see a missing cluster.
  Same end-user symptom the old stale claim described, different, still-live root cause.
  #73 stays open, Phase 1, until an E2E missing-cluster test proves no default is used
  anywhere in the real invocation path (see the table row above).
- **Needs split (table above has the full breakdown):** #71, #72, #82, #84, #86, #87,
  #30, #32.

### Model Armor regional inspect-only test plan (for #30/#32, not yet executed)
**Coverage table to fill in before implementation** — one row per path, each marked
inspected/not-inspected once the trial runs: client→agent · agent→client · agent→Gemini ·
Gemini→agent · GKE Remote MCP request/response · Custom MCP request/response · PagerDuty
payload · final RCA · internal Agent Runtime/gRPC traffic · any unsupported/bypassed path.

**Test cases:** normal PagerDuty-triggered investigation · normal manual investigation ·
prompt-injection · jailbreak · sensitive-data · harmful-content · Kubernetes logs
containing text resembling malicious instructions · Gemini request/response (if routed
through the protected path) · MCP `tools/call` request/response · Custom MCP traffic ·
large tool response · Model Armor service unavailable · confirm nothing is blocked in
inspect-only mode · confirm detection logs + Model Armor spans appear · measure added
latency · verify no TLS/certificate/gateway/MCP/agent regression.

**Decision gate:**
- **Outcome A (regional integration works):** keep Model Armor enabled `INSPECT_ONLY` for
  Phase 1; manage the supported config reproducibly; add monitoring + runbooks; keep
  blocking mode for a future security-approved phase; close #30/#32 only when their exact
  acceptance criteria are proven.
- **Outcome B (still fails):** roll back completely to the last working gateway config;
  keep Model Armor disabled; capture the exact command/API request/response/logs/failure
  point; update the Google support case; document what traffic remains uninspected +
  compensating controls; record a formal accepted risk with security/management sign-off;
  move remaining work to Phase 2; never block the whole investigation workflow while
  experimenting.

### Missing GitHub issues — all four are Phase 1 tracking issues (not created — for your approval first)
Scanning the requirements above against the 36 existing issues, these gaps have no
tracking issue today. **All four are Phase 1**, not Phase 2 candidates — each one gates a
requirement explicitly listed in this doc's Phase 1 sections above, not a post-MVP
enhancement:

1. **PagerDuty webhook signature/dedup/idempotency test suite** — Phase 1, priority
   P1-small-fix. #88 covers building the receiver; the specific negative-test-case list
   (invalid signature, duplicate event, timeout, downstream failure) isn't separately
   tracked and could get dropped if #88 is scoped narrowly. Gates the "PagerDuty" Phase 1
   requirement's test list above.
2. **Model Armor regional inspect-only trial** — Phase 1, priority P1-small-fix (gates the
   #30/#32 controlled trial itself, not just the underlying bugs). #30/#32 are bug reports
   on the *current broken state*; the trial (coverage table, test cases, decision gate) has
   no issue of its own to track as a deliverable with its own acceptance criteria.
3. **Alert-trigger proof pass** — Phase 1, priority P2-medium-fix. "An alert exists in
   Terraform" vs "an alert was triggered and a real notification arrived" are different
   claims; no issue tracks running that proof pass across every required alert. Directly
   gates the Phase 1 production-readiness gate's Observability section above.
4. **Phase 1 operational documentation set** — Phase 1, priority P2-medium-fix. The
   ~25-item doc list above has no tracking issue; without one it's easy for individual PRs
   to each skip "update the docs." Directly gates the Phase 1 readiness gate's Operations
   and documentation section above.

Not filing any of these until you approve the list.

---

## 2026-08-09 update — read this first

Real progress since the 2026-08-05 status below, verified today via 6 parallel live-code
audits (not just a doc pass — includes real `gcloud iam roles describe`, `terraform test`,
and pytest runs):

- **Priority 1 (baseline documentation): now ✅ done.** A full 51+ page knowledge base
  exists under `docs/`, re-audited and corrected today, plus a new master
  [`docs/management/implemented-vs-planned-matrix.md`](docs/management/implemented-vs-planned-matrix.md)
  — this is now the **live, ongoing source of truth for every capability's real status**,
  not the table below. Update that file when a capability's status changes; treat this
  doc's per-priority write-ups below as the historical research they were written from,
  refreshed only where explicitly noted.
- **Priority 5 (deterministic cluster & MCP routing): registry gap closed.** The "thin
  registry" and "missing fields" problems described below are fixed — `var.additional_clusters`
  (Terraform) now carries the full field set (aliases, environment, allowed_namespaces,
  owner, enabled), multi-cluster support works, verified live. Two specific claims below
  are now confirmed **stale, not current bugs**: `context_resolver.py` does **not**
  default to `sre-test-cluster` (re-confirmed via direct code read, twice — 2026-08-08
  and today), and this was likely already wrong when originally written. What's still a
  real, open gap: adding a cluster in a *different* GCP project gets no IAM grant
  (`iac/gke-access` is hardwired to one project).
- **Priority 8 (RCA accuracy validation): baseline established and corrected.** 14 golden
  cases now run clean (0 crashes, was 4/14 failing on an eval-harness bug), 12/14 correct
  tool selection, 100% correct MCP source routing — see
  [`docs/baselines/tool-scaling-baseline-2026-08-09-corrected.md`](docs/baselines/tool-scaling-baseline-2026-08-09-corrected.md).
  Still open: no LLM-judge for root-cause correctness, no confidence calibration
  measurement (both confirmed absent by direct code search, not assumed).
- **Priority 10 (logging/metrics): the "7 log metrics + 3 alerts" count below is stale.**
  Real count today: **11 log-based metrics, 11 alert policies** (`iac/agent/monitoring.tf`,
  counted directly). The specific missing-alert list further down is now shorter than
  written — re-verify against the matrix before treating any single row as still open.
- **Model Armor: status changed since the "MVP blocker, not yet filed" note below.** The
  Google support case referenced there **was filed and answered** (2026-08-07) — see
  `GOOGLE_SUPPORT_RESPONSE_DRAFT_2026-08-07.md` (a reply from us is still pending) and
  `archive/RESOLVED_2026-08-08_MODEL_ARMOR_CONTENT_AUTHZ_TEST.md` (confirmed: no working
  Terraform path exists to wire `CONTENT_AUTHZ` to Agent Gateway — an API-level rejection,
  not a config mistake). **Correction to an earlier draft of this note**: this is not fully
  "resolved to a deliberate tradeoff" — the gateway-level gap is a real, confirmed platform
  limitation (documented, not a mistake), but the app-level fallback (`agent/main.py`'s
  `_sanitize()`) is ALSO currently inactive in the live config, since it only activates
  when the gateway is *off* (`enable_agent_gateway=false`) and the live deployment has the
  gateway *on* — meaning **Model Armor filters nothing today, at either layer**. That's a
  real, open gap, not something to treat as settled. What's directly actionable and not yet
  applied: Google's own tuning guidance (confidence threshold to `HIGH`, check org floor
  settings, split templates) — but applying it doesn't by itself close the "nothing is
  active right now" gap. See [Security Operations](docs/governance/security.md) for the
  full picture and current compensating controls.
- **Not re-verified today, status below still assumed current**: PagerDuty integration
  (still nothing built), Connect Gateway on-prem connectivity, cost validation specifics.
  Don't treat their unchanged 🟡/⬜ marks as freshly confirmed — they're carried forward,
  not re-audited in this pass.

**Objective:** controlled production rollout as a **read-only** Kubernetes investigation assistant, triggered by PagerDuty, covering **GKE and on-prem/non-GKE** clusters, with deterministic cluster→MCP routing. Do not rebuild working components without a confirmed issue.

Legend: ✅ done · 🟡 partial · ⬜ not started

## Status at a glance (table below dated 2026-08-05 — see the "2026-08-09 update" section above for what changed since; going forward, [`docs/management/implemented-vs-planned-matrix.md`](docs/management/implemented-vs-planned-matrix.md) is the live status source, not this table)
| # | Priority | Status (2026-08-05) | 2026-08-09 change |
|---|---|---|---|
| — | **Model Armor resolution** | 🟥 blocked — case not yet filed | 🟡 case filed + answered; actionable tuning plan pending; gateway-level CONTENT_AUTHZ confirmed no Terraform path exists |
| 1 | Document & protect working baseline | 🟡 partial | ✅ **done** — full knowledge base + status matrix built and audited |
| 2 | PagerDuty incident integration | ⬜ not started | unchanged |
| 3 | On-prem connectivity (Connect Gateway) | ⬜ not started | unchanged |
| 4 | Custom read-only Kubernetes MCP server | 🟡 partial | unchanged (built, not deployed) |
| 5 | Deterministic cluster & MCP routing | 🟡 partial | ✅ registry/multi-cluster gap **closed**; cross-project IAM for a different GCP project still open |
| 6 | End-to-end routing tests | ⬜ not started | unchanged |
| 7 | Confidence score redesign | 🟡 built, not ported to company repo | unchanged |
| 8 | Minimum RCA accuracy validation | 🟡 partial (eval harness exists) | 🟡 corrected baseline established (14/14 clean run); LLM-judge + calibration still absent |
| 9 | Production security validation | 🟡 partial (strong baseline) | unchanged |
| 10 | Logging/metrics/alert validation | 🟡 partial (7 metrics + 3 alerts claimed) | 🟡 real count is **12 metrics + 12 alerts** as of 2026-08-20 (was 11+11 when this row was written) |
| 11 | Cost validation & optimization | 🟡 partial | unchanged |
| 12 | Controlled production launch | ⬜ gated on all of the above | unchanged |

---

## Priority 1 — Document & protect the working baseline  🟡
**Have:** `archive/RESOLVED_2026-07-17_CURRENT_STATE.md`, `archive/RESOLVED_2026-07-17_RCA_REPORT.md`, `archive/RESOLVED_2026-07-17_FINAL_RCA.md`, `archive/RESOLVED_2026-07-17_AUDIT_REPORT.md`, `README.md`; git clean on `main@897e48b`; backup made (`~/projects/_backups/testing2-...-BACKUP-2026-07-28.tar.gz`). Confidence + routing logic now documented (this scan).
**Missing:** one consolidated production-baseline doc (architecture + Agent Engine/Gateway/MCP/identity config + cross-project flow + rollback path in one place); explicit rollback procedure; separate branches/envs for Connect-Gateway / custom-MCP / PagerDuty testing.
**Acceptance:** a single BASELINE.md a new engineer can read to understand + safely roll back; rollback tested once.
**Depends on:** none (do first).

## Priority 2 — PagerDuty incident integration  ⬜
**Have:** nothing — repo-wide grep finds **zero** PagerDuty code/IaC (only prose in NEXTSTEPS.md item #4 and a docstring in `invoke_agent.py:240`).
**Build:** authenticated webhook receiver (Cloud Run) → validate PagerDuty signature → normalize fields → extract routing hints (cluster, env, project, namespace, workload, service, region, alert source) → dedup + idempotency + correlation ID → trigger **read-only** investigation → return RCA **draft** for human review. Define behavior when routing info is insufficient.
**Acceptance:** a signed PD webhook starts one investigation; replayed/duplicate events are ignored; an event with no cluster info does **not** guess — it stops or asks. Remediation stays out of the flow.
**Depends on:** 5 (routing) for cluster resolution; can start the receiver independently.

## Priority 3 — On-prem connectivity via GKE Fleet Connect Gateway  ⬜
**Have:** nothing — no Fleet/`gkehub`/Connect-Gateway code or IaC. Only routing comments anticipate `cluster_type != gke`.
**Build:** register a test non-GKE cluster with GKE Fleet → validate Connect Gateway access with **short-lived creds** (no stored kubeconfig) → validate IAM + K8s RBAC (read-only) → confirm audit logging → measure latency/reliability → document onboarding/removal + failure behavior. Confirm the intended architecture with Google where docs are unclear.
**Acceptance:** agent reaches a registered non-GKE cluster read-only via Connect Gateway using short-lived creds; onboarding + outage behavior documented.
**Depends on:** none technically; must exist before 4 is useful for on-prem.

## Priority 4 — Custom read-only Kubernetes MCP server  🟢
**Have:** `mcp/server.py` (FastMCP) with **27 read-only tools** — full parity with `agent/mcp_client.py`'s `CUSTOM_K8S_TOOLS` allowlist (Pod, Deployment/ReplicaSet, StatefulSet/DaemonSet, Service/Endpoints, Node, ConfigMap, HPA, PVC, Job, namespace-wide events). New tool logic in `mcp/tools/{workloads,services,nodes,configmaps,scaling,storage,jobs}.py` + extended `deployments.py`/`events.py`. Hardening centralized in `mcp/security.py` (validation, namespace scope limits, response trimming, secret redaction, timeouts, response-size limits, rate limiting, audit logging) applied to every tool via a `@guarded()` decorator; health checks at `/healthz` (liveness) and `/readyz` (real connectivity check). `get_k8s_clients()` now also supports a `K8S_MCP_KUBE_CONTEXT` kubeconfig-context mode for GKE Fleet Connect Gateway (P3) — proven live: all 27 tools invoked through `server.mcp._call_tool_mcp` against context `connectgateway_sreagent-t2-demo_global_sre-lab` reaching the real `sre-lab` cluster; 18/19 spot-checked tools returned real data, `list_nodes` correctly surfaced the documented `view`-role 403 as a structured `{"error": ...}` instead of crashing. Output envelope proven identical to GKE Remote MCP via a real side-by-side call through `agent/mcp_client.call_tool()` against both sources (`sre-test-cluster` on GKE, `sre-lab` via Connect Gateway) — both return the exact same 5-key wrapper (`ok`, `result`, `tool`, `mcp_source`, `duration_s`); internal `result` payload shape differs (GKE Remote returns kubectl-describe text, custom MCP returns structured JSON) but that's opaque to the reasoning layer, which LLM-summarizes `result` regardless of its internal shape (`agent/nodes/evidence_extractor.py`). 91 tests passing (`mcp/tests/`, incl. 7 live-cluster integration tests) — see `docs/custom-k8s-mcp.md` for full evidence.
**Not yet done:** the Cloud Run deployment itself (`iac/agent/cloudrun_mcp.tf`) is not wired to reach clusters via Connect Gateway in production — that needs `gke-gcloud-auth-plugin`/`gcloud` baked into `mcp/Dockerfile`'s slim image plus a `roles/gkehub.gatewayReader` binding on the runtime SA (`docs/connect-gateway-onprem.md`'s "Open items"); today's proof is the code path + a live local run against Connect Gateway, not a redeployed Cloud Run service. Rate limiting is in-process only (no distributed limiter across Cloud Run instances — acceptable for a single-tenant internal tool per `mcp/security.py`'s design note).
**Acceptance:** same logical capabilities as GKE Remote MCP — met (27/27 tool names match the allowlist). No write/exec/port-forward — met, verified by a permanent regression test (`mcp/tests/test_no_mutation.py`) plus manual grep, zero mutating K8s API calls anywhere in `mcp/`. Validated + rate-limited + audited + tested — met. Output shape identical to GKE-MCP — met at the envelope level (the level the reasoning layer actually consumes), proven with a real side-by-side call, not code review alone.
**Depends on:** 3 (for on-prem reach) — done.

## Priority 5 — Deterministic cluster & MCP routing  ✅ (registry gap closed 2026-08-09; original write-up below kept for history)
**2026-08-09: the registry and safety-default problems described below are fixed.** `var.additional_clusters` (`iac/agent/variables.tf`) is now `map(object(...))` with the full field set (aliases, project, region, type, environment, allowed_namespaces, owner, enabled), rendered into `clusters.json` by Terraform (`iac/agent/main.tf`) — no manual GCS edits, no thin schema. Real collision-guard `validation` block, Terraform-native tests (`iac/agent/tests/clusters_json.tftest.hcl`, 4/4 passing live) and Python routing tests (`tests/test_multi_cluster_registry.py` + `test_mcp_router.py`, 13/13 passing live). Separately confirmed via direct code re-read (twice: 2026-08-08 and 2026-08-09) that `context_resolver.py` does **not** default a missing cluster to `sre-test-cluster` — that specific claim below was already stale/wrong when checked, not a bug introduced since. Still genuinely open: adding a cluster in a **different** GCP project gets no IAM grant (`iac/gke-access` is hardwired to one `project_b_id`) — see `docs/runbooks/add-gke-cluster.md`.

**Original write-up (2026-08-04, kept for history):**
**Have (good):** MCP **source** selection is **deterministic** — `mcp_router.py:81-88`: `cluster_type=="gke" → gke_remote_mcp else k8s_mcp`; tool pick within a source is model-based but constrained by a hard allowlist (`mcp_router.py:140-145`). Selected cluster/MCP recorded in the structured `obs_event` (`main.py:455-461`).
**Registry (thin):** `gs://…/clusters.json` (`mcp_client.py:134-169`), seeded by `clusters.json.tftpl`. Fields today: `name, project, region, type(+optional mcp_url)`. **Missing registry fields:** aliases, environment, allowed namespaces, ownership, **enabled/disabled**, canonical ID, per-cluster MCP destination.
**Safety gaps:** unknown/ambiguous handling is inconsistent — `context_resolver.py:27-37` **defaults a missing cluster to `sre-test-cluster`**; `mcp_router.py:84-88` **silently falls back to gke** if the registry is empty. No recorded rationale for the source decision.
**Build:** authoritative registry with the full field set + routing priority (exact ID → verified PD metadata → approved alias → project/env/namespace → human); reject disabled/unknown; **stop safely on ambiguity (no defaulting, no silent gke fallback)**; record why a cluster+MCP were chosen; put it in RCA metadata; prevent the model from picking arbitrary MCP endpoints.
**Acceptance:** the safety matrix in P6 passes; never defaults or guesses a cluster.
**Depends on:** registry redesign; feeds 2 and 6.

## Priority 6 — End-to-end routing tests  ⬜
**Have:** an RCA eval harness (`agent/eval/golden_cases.py`, `run_eval.py`) but **no** routing-safety scenarios.
**Build the scenarios:** GKE→remote MCP; non-GKE→custom MCP; cross-project correct selection; similar-named clusters not confused; unknown rejected; missing metadata → no guessed access; conflicting metadata → explicit error; disabled cluster blocked; MCP/Connect-Gateway outage handled; evidence proven to come from the selected cluster; RCA names exact cluster/ns/workload; no cross-cluster evidence bleed.
**Acceptance (the one that matters):** the agent **never silently investigates the wrong cluster**.
**Depends on:** 2, 4, 5.

## Priority 7 — Confidence score redesign  🟡 (exists, but has the exact flaws you flagged)
**Current logic (evidence):** single `confidence` float, **both** model + app. Deterministic cap is driven by **raw evidence count** — `coverage_score = min(1, evidence_count/4)`, `cap = coverage+0.25` (`task_evaluator.py:92-100`). Bands hard-coded 0.85/0.65 (`task_evaluator.py:104-110`, **not configurable**). Completeness and root-cause certainty are **conflated** into one number. **Not penalized:** failed tools (only indirect), contradicting evidence (no detection at all), ambiguous cluster identity. RCA text **duplicates root cause 3×** (`main.py:231/238/501`). **No fields** for contradicting evidence or alternative explanations; `evidence_gaps` gives partial "missing evidence".
**Build:** split into **Investigation Completeness** (did we collect expected evidence — cluster confirmed, route confirmed, required tools done, logs/events/status/config collected, freshness, failed tools) and **Root-Cause Confidence** (direct evidence, multiple independent sources, tied to exact resource, in time window, contradictions, alternatives, missing evidence, inference-vs-observation). Model proposes cause + reasoning; **app computes final confidence deterministically**; penalize failed/missing/stale/contradictory evidence + ambiguous cluster + unresolved alternatives; cap when logs-only / resource uncertain / dependency unchecked; support insufficient/unknown/multiple-cause. Make bands configurable. Fix the 3× RCA duplication; add contradicting-evidence + alternatives fields.
**Acceptance:** two separate scores in the RCA; confidence drops on missing/failed/stale/contradictory evidence; agent returns "unknown" rather than inventing a cause.
**Depends on:** 1 (document current — done here); informs 8, 10.

## Priority 8 — Minimum RCA accuracy validation  🟡
**Have:** `agent/eval/` golden-cases + runner.
**Build:** scenario matrix — CrashLoopBackOff, ImagePullBackOff, OOMKilled, Pending, one non-GKE via custom MCP, one insufficient-evidence, one conflicting-evidence, one ambiguous-routing, one MCP/Connect-Gateway failure. Deterministic checks: correct cluster/MCP/ns/workload, claims cite collected evidence, unsupported claims excluded, confidence drops when evidence missing, returns "unknown" vs inventing, remediation stays advisory. LLM-judge optional secondary.
**Acceptance:** all scenarios pass the deterministic checks.
**Depends on:** 7, 5.

## Priority 9 — Production security validation  🟡 (strong baseline)
**Confirmed good:** `identity_type = AGENT_IDENTITY` (`agent_engine.tf:98`, no static SA); IAP **REQUEST_AUTHZ** on the gateway (`agent_gateway.tf:88/107`); read-only cross-project GKE roles (`gke-access/crossproject_iam.tf`).
**Gaps to validate/document:** Model Armor is **NOT** wired at the gateway (comments claim it but no CONTENT_AUTHZ resource) — document as a **known control gap** with compensating controls (read-only tools, structured responses, input validation, output trimming, evidence grounding, bounded loops, human review). Log redaction is weak (raw tool errors/facts logged). Add: PagerDuty webhook auth, custom-MCP authz, Connect-Gateway IAM+RBAC, "MCP endpoints not arbitrarily selectable", secrets-not-in-prompts/logs/RCA, evidence isolation per investigation/cluster, human approval for prod deploys. **Do not** describe IAP/Gateway as content-inspection controls.
**Acceptance:** each item confirmed against code/live config; accepted risks + open Google questions documented.
**Depends on:** 2, 4, 5 to exist first.

## Priority 10 — Logging, metrics & alert validation  🟡
**Correction (2026-08-09):** the "7 log metrics + 3 alerts" count below is stale — counted
directly against `iac/agent/monitoring.tf` today: **11 log-based metrics, 11 alert
policies**. Re-check the "Missing alerts" list below against the real resource list before
treating any specific row as still open; several may already exist.

**Have (2026-08-04 write-up, count superseded above):** `monitoring.tf` — 7 log metrics (`invocations, errors, escalations, investigation_cost_usd, confidence_band, loop_exit_reason, tool_failures`) + 3 alerts (error-rate, escalation-rate, cost-spike) + email channel. Rich per-investigation structured log (`rca_builder` `sre-agent-investigations`).
**Missing visibility:** trace_id correlation, cluster-routing reason, MCP/model/total **latency** (per-tool `duration_s` is captured then discarded), agent-gateway + connect-gateway failures, investigation completeness, evidence-storage success/failure, a top-level `status` field (so the `errors` metric may never fire from RCA-path failures), PagerDuty incident id.
**Missing alerts (all 11 required):** PD webhook failures, routing failures, unknown/ambiguous clusters, GKE-MCP failures, custom-MCP failures, connect-gateway failures, agent-gateway failures, excessive latency, repeated tool failures, loop/token termination, evidence-storage failures. (Note: `tool_failures`/`loop_exit_reason` metrics exist but have **no** alert.)
**Acceptance:** required fields emitted; alerts exist AND are **proven** by triggering controlled failures (not "Terraform resource exists").
**Depends on:** 5, 7 (needs routing reason + completeness/confidence fields).

## Priority 11 — Cost validation & optimization  🟡
**Have:** per-investigation token + cost logged; cost-spike alert; per-node token JSON to stdout.
**Missing:** cost per successful vs failed investigation, model/token usage **by node** aggregated, repeated/unnecessary tool-call detection, oversized-response trimming, evidence dedup, configurable per-investigation token+cost limits, a realistic monthly estimate. Measure before optimizing; don't weaken evidence/security/confidence.
**Acceptance:** cost-per-investigation measured + bounded by config limits; monthly estimate produced.
**Depends on:** 1; before 12.

## Priority 12 — Controlled production launch  ⬜
Read-only, human-reviewed, no auto-remediation, limited clusters + engineers, auditable, tested rollback. **Launch gate = all criteria in P1–P11 met.**
**Depends on:** 1–11.

---

## Required execution order — REVISED 2026-08-04, supersedes the order below

Business-priority reorder, verified against every Priority's own "Depends on" line so nothing
executes ahead of what it needs (see [[app-infra-gateway-parity-build]] memory / session
2026-08-04 for the full dependency check):

1. **Model Armor resolution** — root-cause + fix. Carved out of P9; now an MVP/business-critical
   blocker per management + security team decision (see Cross-cutting notes). Independent of the
   rest — no dependency on PagerDuty/routing/Custom-MCP.
2. Confidence score redesign (P7) — self-satisfied dependency (P1 "done here").
3. Deterministic cluster & MCP routing (P5) — no blocking dependency, moved up specifically to
   unblock 4, 5, and 7 below.
4. Minimum RCA accuracy validation (P8) — needs 2 + 3, now satisfied.
5. Logging/metrics/alert validation (P10) — needs 3 + 2, now satisfied.
6. On-prem connectivity — Connect Gateway (P3) — no dependency, moved up to unblock 7.
7. Custom read-only Kubernetes MCP server (P4) — needs 6, now satisfied.
8. End-to-end routing tests, **pass 1** (P6) — everything not requiring PagerDuty; needs 5(old
   4)+7, now satisfied. PagerDuty-triggered scenarios deferred to pass 2.
9. PagerDuty incident integration (P2) — needs 3 (cluster resolution via routing), satisfied.
10. End-to-end routing tests, **pass 2** (P6, complete) — re-run now that PagerDuty exists;
    closes the one dependency pass 1 couldn't cover.
11. Remaining: Document & protect working baseline (P1), rest of Production security validation
    (P9, minus Model Armor which is done at step 1), Cost validation & optimization (P11).
    **Do P1 early within this group** — P11 depends on it, and it's the rollback-safety doc;
    don't leave it for last.
12. Controlled production launch (P12) — gate, needs everything above done.

## Original execution order (superseded 2026-08-04, kept since Priority write-ups above use these numbers)
1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12.
**Do NOT** start broad MCP expansion (Prometheus/Grafana/Elastic/Confluence) or the ADK migration until the controlled rollout is stable (Post-Launch).

## Post-launch (do not block rollout)
More clusters; Prometheus/Grafana/Elastic/Cloud-Logging MCP; runbook/Confluence sources; expand golden dataset; confidence calibration from engineer feedback; automated PD updates; richer dashboards; revalidate Model Armor; **LangGraph → Google ADK migration (last)**.

## Cross-cutting notes
- **Model Armor — status as of 2026-08-09 (updates the 2026-08-04 note below).** The Google
  support case referenced below **was filed and answered** (2026-08-07 — see
  `GOOGLE_SUPPORT_RESPONSE_DRAFT_2026-08-07.md`, our reply is still pending). Google's
  proposed mTLS root cause was checked against this repo's own evidence and rejected (see
  `archive/RESOLVED_2026-07-17_FINAL_RCA.md`'s 2026-08-07 addendum — re-tested live, no
  difference). Separately confirmed (2026-08-08 live test,
  `archive/RESOLVED_2026-08-08_MODEL_ARMOR_CONTENT_AUTHZ_TEST.md`): there is **no working
  Terraform path** to wire `CONTENT_AUTHZ` to Agent Gateway — an API-level rejection
  (`Error 400: unsupported Google API for AuthzExtension`), not a config mistake. What
  *is* directly actionable, from Google's own guidance, and not yet applied: set
  prompt-injection/jailbreak confidence to `HIGH` on the operational-data template, check
  org-level floor settings first (they can override), split input/output and
  log-ingestion/chat templates. **Important correction**: the gateway-level gap is
  documented and understood, but the app-level fallback is *also* currently inactive in
  the live config (it only runs when the gateway is off) — Model Armor filters nothing
  today at either layer. Applying Google's tuning guidance alone does not close this; see
  [Security Operations](docs/governance/security.md) for the full picture.

- **Original 2026-08-04 note (superseded above, kept for history): Model Armor is now an MVP blocker.** Originally scoped as a documented
  control gap with compensating controls, not a to-fix-now blocker (P9). **Reversed per
  management + security team decision (meeting, week of 2026-07-28): security requires it before
  any production rollout, to avoid attack risk at launch.** Root cause not yet identified — it's
  currently disabled because enabling it broke mTLS and produced false positives on K8s logs
  (open Google ticket, ticket ref not on file). Must be root-caused, not just re-enabled blindly.
- The custom MCP and GKE Remote MCP must emit a **common evidence format** (P4) so the reasoning layer never depends on the MCP implementation.
- Routing is deterministic for **source**; keep the model out of final cluster/endpoint selection (P5).
