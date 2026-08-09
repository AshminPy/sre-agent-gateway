# Tool-Scaling Baseline — CORRECTED — 2026-08-09

> **Status: this is the official tool-scaling baseline.** It supersedes
> `tool-scaling-baseline-2026-08-09.md` (kept, unmodified, as historical evidence — see
> "Relationship to the first run" below).
>
> **Purpose**: same as the first run — capture routing/tool-selection/RCA numbers with 2
> MCP sources (only 1 exercised — GKE Remote MCP) so a real MCP source #3 can be added
> later and compared against this baseline. No new infrastructure, no routing redesign,
> no new MCP source, no confidence-calibration work — this is a data/config correction
> and a clean rerun only, per the explicit scope for this follow-up.
> **Raw data**: `tool-scaling-baseline-2026-08-09-corrected-raw.json` in this same directory.
> **Live cluster used**: `sre-test-cluster` (existing GKE Autopilot cluster) — no new cluster.

## What changed since the first run

Two real problems in the first baseline's *evaluation harness* (not the agent) were fixed:

1. **Stale expected tool names** — `agent/eval/golden_cases.py`'s `expected_trajectory`
   fields were written against an old tool-naming scheme. Fixed by reading the current
   real tool definitions (`agent/mcp_client.py`'s `GKE_REMOTE_TOOLS` / `CUSTOM_K8S_TOOLS`)
   and rewriting all 12 GKE-routed cases' expected names to match. The 2 cases that were
   already correct (`onprem-001`, `ambiguous-routing-001`) were left unchanged, with a
   comment explaining why.
2. **Recursion-limit mismatch** — local eval mode was silently using LangGraph's built-in
   default of 25, while the deployed agent uses 60. Fixed with a single shared constant
   (`agent.graph.GRAPH_RECURSION_LIMIT = 60`), imported by both `agent/main.py` and
   `agent/eval/run_eval.py` — not two numbers kept in sync by hand.
3. **Regression tests added** so both issues are guarded against returning silently:
   `tests/test_golden_cases_tool_names.py` (fails if any case's `expected_trajectory` uses
   a tool name from the wrong MCP source's naming scheme) and
   `tests/test_recursion_limit_consistency.py` (fails if either call site hardcodes its own
   `recursion_limit` integer instead of importing the shared constant).
4. A related silent gap found while fixing #2 was also closed: `run_local()`/`run_remote()`
   already computed per-case latency but `score_case()` was dropping it before it reached
   the saved JSON. Added `"latency_seconds"` to the saved score — see the Latency section
   below for what this does and doesn't measure.

## Headline numbers — old (broken harness) vs new (corrected harness)

| Metric | v1 (2026-08-09, broken harness) | v2 (2026-08-09, corrected — this run) |
|---|---|---|
| MCP sources registered | 2 (`gke_remote_mcp`, `k8s_mcp`) | 2 (unchanged) |
| MCP sources actually exercised | 1 (`gke_remote_mcp`) | 1 (`gke_remote_mcp`) — `onprem-001` (`k8s_mcp`) made **zero** tool calls this run, see Finding C |
| Tools available (exercised source) | 6 (`GKE_REMOTE_TOOLS`) | 6 (unchanged) |
| Tools available (un-exercised source) | 27 (`CUSTOM_K8S_TOOLS`) | 27 (unchanged) |
| Cases run | 14 of 14 | 14 of 14 |
| Cases that completed execution (no crash) | 10 / 14 (4 hit the recursion-limit bug) | **14 / 14** — recursion-limit fix confirmed working, 0 crashes |
| Cases passing the harness's full pass/fail gate (recall≥0.5 AND keywords≥0.5 AND confidence AND outcome AND max-confidence, all required) | not meaningfully comparable — trajectory scoring was 0% across the board from stale names | **3 / 14** — see "Reading the pass/fail number correctly" below, this is NOT a regression |
| Cases with correct/expected tools actually called (trajectory recall ≥ 0.5) | not measurable (stale names) | **12 / 14** |
| Cases with every expected tool called (trajectory recall = 1.0) | not measurable (stale names) | **7 / 14** |
| Correct MCP **source** selection | 10 / 10 completed cases — 100% | 12 / 12 cases that made ≥1 tool call — 100% (no case called a tool from the wrong source's naming scheme) |
| Unnecessary/wrong tool calls (wrong-source tool name used) | 0 detected | 0 detected |
| Failed tool calls (real execution errors) | 0 | 0 |
| Routing safe-stops | 2 (both correct) | 2 (`ambiguous-routing-001` correct safe-stop; `onprem-001` made 0 calls — see Finding C, not the same as a clean safe-stop) |
| Total LLM calls | 157 | 159 |
| Total tokens | 126,804 | 137,377 |
| Total cost | $0.0301 | $0.0339 |
| Iterations | mostly 3-4/case; 4 cases hit the 25-step ceiling | mostly 3-4/case; 0 cases hit any ceiling |
| Latency | not captured (known gap in v1) | **captured this run** — see Latency section |

## Reading the pass/fail number correctly (3/14) — this is not a regression from v1

`score_case()`'s `passed` field requires ALL of: trajectory recall ≥ 0.5, keyword accuracy
≥ 0.5, confidence above the case's minimum, outcome matches (if specified), and confidence
below the case's maximum (if specified) — a strict AND across five checks. v1 could not
produce a meaningful number here at all, because the stale tool names made trajectory
recall effectively unmeasurable (comparing real tool names against tool names that no
longer exist). v2's 3/14 is the **first real reading** of this gate, not a drop from a
previous good number. The two metrics that actually matter for "is tool selection
working" are:

- **Trajectory recall ≥ 0.5 (right tools called): 12 / 14 — this is the real headline result.**
- **Correct MCP source selection: 100%** (unchanged from v1, still deterministic code, not model-guessed).

The low pass/fail count is driven almost entirely by **keyword accuracy** (avg 0.321) and
by **confidence** falling under 0.65 on 3 clean, correctly-solved cases (see Finding A) —
not by wrong tool selection.

## Three new findings from the corrected run — read before trusting the raw numbers further

### Finding A: keyword accuracy is now the real bottleneck, and it looks like RCA-text phrasing, not wrong reasoning

7 of 14 cases scored 0.0 keyword accuracy despite the underlying root cause being
correct. Examples, checked by hand:

- `crashloop-001`: root cause says "crashing with exit code 1" — matches the expected
  keyword `exit`, but never uses the literal string `CrashLoopBackOff` even though that
  is the pod's actual status reason. kw_acc = 0.5, not 0.0, but same pattern.
- `pending-001` / `mcp-gateway-failure-001` / `conflicting-evidence-001`: these three
  target pods (`batch-worker-7`, `edge-gateway-1`, `shared-cache-2`) **have no live k8s
  fixture in this repo** — they don't exist on the cluster, in v1 either. The agent
  correctly concludes "pod does not exist," which is a reasonable, grounded answer, but
  it cannot match keywords like `Pending`/`FailedScheduling`/`conflicting` that assume a
  fixture exists to produce that evidence. **This is a pre-existing gap, not something
  introduced by this fix** — `k8s/`, `k8s/demo/` were searched for these three pod names;
  none exist. Flagged here, not fixed (adding fixtures/cluster infra is out of the
  approved scope for this follow-up).
- `cascading-001`: real gap, not a text-matching artifact — the agent correctly identifies
  `order-api` has 0 ready replicas but doesn't dig into `order-db` (the true upstream
  cause the fixture is designed to require investigating). Expected keywords `database`,
  `order-db`, `OOMKilled` never appear. `expected_trajectory` for this case is explicitly
  documented as "the MINIMUM for the first pod investigated" — this result is consistent
  with that comment, not a contradiction of it.

### Finding B: one case picked the wrong resource under shared-namespace conditions — real, verified, not fixed here

`selector-001` (query: "notification-svc shows no endpoints") returned a root cause
entirely about `auth-service`'s missing ConfigMap — a different case's pod. Verified
directly, not guessed: re-ran this one case in isolation and inspected
`tool_history`/`evidence_store`. `list_k8s_events` was called with **no resource-name
filter**, returning every event in the shared `test-incidents` namespace — including
`auth-service`'s loud, repeating ConfigMap error (23 occurrences over 31 minutes,
alongside `crashloop-pod`, `imagepull-pod`, and `order-api` events from the *other*
golden cases' own fixtures, since all 7 `test-incidents` fixtures are live at once for
this rerun). `describe_k8s_resource`/`get_k8s_resource` were then also scoped to
`auth-service`, not `notification-svc` or `notification-deployment`. Root cause: this
case's `resource_hints` (`golden_cases.py`) has no `pod` field at all — the target
(`notification-svc`) is only named inside the free-text `user_query`, and with no
resource-name hint plus a namespace-wide event query, the model latched onto the
loudest unrelated signal in the shared namespace instead. This is a real, reproduced
agent-behavior finding, not an eval-harness artifact — but diagnosing/fixing it would be
prompt or routing-logic work, which is explicitly out of scope for this follow-up
("do not redesign the routing architecture"). Recorded here as a limitation of this
baseline (all fixtures sharing one namespace can cross-contaminate cases with no
explicit resource hint) and as a real product-quality lead for later.

### Finding C: `onprem-001` made zero tool calls this run

v1 reported `onprem-001` as a correct safe-stop. This run it made **0 tool calls** and
returned "No evidence was extracted" — different from a clean, by-design safe-stop like
`ambiguous-routing-001` (which is *supposed* to make 0 calls; `onprem-001` is supposed to
make 3, against the custom K8s MCP). No exception or blocked-call message was logged for
this case. Not further diagnosed here — this sits squarely on backlog item #4 (verifying
whether the custom MCP / `k8s_mcp` path is actually reachable, given the
`ENABLE_CUSTOM_MCP=true` GitHub variable found earlier this session), which is already
next after this baseline work. Flagging it now so it isn't lost, not treating it as solved.

## Latency — captured this run, with a caveat on what it measures

`latency_seconds` per case now reaches the saved output (the v1 gap is closed). This is
**harness-side wall-clock time** for `graph.invoke()` in local in-process mode — it
includes real Gemini API round-trips and real GKE tool calls, but it is not the same
number as the deployed agent's own `total_latency_s` telemetry (`rca_builder.py`), which
only populates on the live/remote path. Range: 4.0s (safe-stop cases, no tool calls) to
141.2s (cases with Gemini rate-limit backoff — several cases hit "Rate limited — waiting
30s before retry" during this run, which inflates wall-clock time without reflecting real
production latency). Sum across all 14 cases: ~14.3 minutes (sequential, single-process).
If a real end-to-end production latency number is needed for the source-#3 comparison,
`--mode remote` against the deployed engine is still the accurate way to get it — not
done here, same as v1's note.

## What worked correctly, confirmed by direct evidence

- **Recursion-limit fix confirmed**: 0 of 14 cases hit any recursion ceiling this run, vs
  4 of 14 in v1. Direct before/after proof of the fix working.
- **Correct MCP source selection**: 100% of cases that made at least one tool call used
  only tool names belonging to the correct source's naming scheme — no case called a
  `CUSTOM_K8S_TOOLS` name for a GKE-routed case or vice versa.
- **Zero tool execution failures** across all 14 cases — every tool call that was
  attempted returned a real result, no transport/auth errors.
- **`ambiguous-routing-001` safe-stop still correct**: 0 tool calls, `insufficient_evidence`,
  confidence 0.0 — exact match to its own `expected_outcome`/`max_confidence`.

## Remaining evaluation limitations (carried forward + new)

1. No live fixture exists for `batch-worker-7` (`pending-001`), `edge-gateway-1`
   (`mcp-gateway-failure-001`), or `shared-cache-2` (`conflicting-evidence-001`) — these
   three cases can only ever produce a "pod does not exist" result in this environment,
   which is a legitimate agent answer but can't be scored against their intended scenario.
   Pre-existing gap, not introduced by this fix.
2. `mcp-gateway-failure-001`'s name implies a simulated GKE Remote MCP failure, but there
   is no actual fault injection in local eval mode — it currently just re-tests "pod
   doesn't exist" behavior, same as #1.
3. Finding B (shared-namespace cross-contamination for hint-less cases) and Finding C
   (`onprem-001`'s silent zero-call result) are both real and unresolved — neither was in
   scope to fix under this follow-up's constraints.
4. Latency numbers this run are inflated by live Gemini rate-limiting during the run
   (visible as repeated "Rate limited — waiting 30s" in the raw log) — treat the latency
   range as this-run-specific, not a tight production estimate.
5. `--mode local` still cannot capture the deployed agent's own `total_latency_s`
   telemetry — only `--mode remote` can, same caveat as v1.

## Relationship to the first run

`tool-scaling-baseline-2026-08-09.md` and `tool-scaling-baseline-2026-08-09-raw.json`
are preserved unmodified as historical evidence of what the *broken* harness reported,
and of the two findings that motivated this corrected rerun. Do not delete or edit them.
This file is the one to use for any future source-#3 comparison.

## How to re-run this exact baseline later (for the source-#3 comparison)

```bash
source scripts/init-env.sh
export CLUSTER_CONFIG_BUCKET=$(terraform -chdir=iac/agent output -raw cluster_config_bucket_name)
export EVAL_BUCKET=$(terraform -chdir=iac/agent output -raw eval_bucket_name)
# Ensure k8s/*.yaml and k8s/demo/*.yaml fixtures are freshly applied to
# test-incidents / demo-incidents first — a self-healed or missing fixture
# gives a false result (see Finding B above for why shared-namespace state matters).
python3 -m agent.eval.run_eval --mode local --cases all --output docs/baselines/tool-scaling-baseline-<date>-raw.json
```
