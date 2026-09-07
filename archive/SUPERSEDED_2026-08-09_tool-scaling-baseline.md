> **SUPERSEDED 2026-08-09 (archived 2026-09-07).** This is the first ("broken harness") run of
> the tool-scaling baseline. Its own sibling,
> [`docs/baselines/tool-scaling-baseline-2026-08-09-corrected.md`](../docs/baselines/tool-scaling-baseline-2026-08-09-corrected.md),
> explicitly states it "supersedes" this file and is "the official tool-scaling baseline" —
> use that file for any future source-#3 comparison. **Caveat carried over from the corrected
> file's own text:** it says this file (and its raw JSON,
> `docs/baselines/tool-scaling-baseline-2026-08-09-raw.json`, not moved here) should be "kept,
> unmodified, as historical evidence" of what the broken harness reported. This archive copy
> does not delete or edit the original — the original file remains in place at
> `docs/baselines/tool-scaling-baseline-2026-08-09.md` alongside its raw JSON. Content below is
> reproduced unmodified.

---

# Tool-Scaling Baseline — 2026-08-09

> **Purpose**: capture today's routing/tool-selection/RCA numbers with 2 MCP sources (only 1 exercised — GKE Remote MCP) so a real MCP source #3 can be added later and re-compared against this same baseline. No new infrastructure or framework was built for this — this run uses the existing `agent/eval/run_eval.py` harness and the existing 14 golden cases (`agent/eval/golden_cases.py`), `--mode local`.
> **Raw data**: `tool-scaling-baseline-2026-08-09-raw.json` (full eval output) in this same directory.
> **Live cluster used**: `sre-test-cluster` (the existing GKE Autopilot cluster) — no new cluster created, per instruction.

## Headline numbers

| Metric | Value |
|---|---|
| MCP sources registered | 2 (`gke_remote_mcp`, `k8s_mcp`) |
| MCP sources actually exercised today | 1 (`gke_remote_mcp` — the only source reachable, since the custom MCP's live status is under active re-verification, see note below) |
| Tools available in the exercised source | 6 (`GKE_REMOTE_TOOLS`) |
| Tools available in the un-exercised source | 27 (`CUSTOM_K8S_TOOLS`) — not exercised |
| Cases run | 14 of 14 golden cases |
| Cases completed without error | 10 / 14 |
| Cases that hit an eval-harness error (not an agent/tool failure) | 4 / 14 — see finding #2 below |
| Correct MCP **source** selection | 10 / 10 completed cases — 100% |
| Correct MCP **tool** selection (by literal golden-dataset match) | 0 / 10 — see finding #1, this number is misleading, read the finding |
| Unnecessary/wrong tool calls observed | 0 detected |
| Failed tool calls (real execution failures) | 0 |
| Routing failures / safe-stops | 2 (both correct — see below) |
| Total LLM calls | 157 across 14 runs |
| Total tokens | 126,804 |
| Total cost | $0.0301 (~$0.0022/case average) |
| Iterations (loop steps) | Mostly 3-4 per completed case; 1 (safe-stop, no loop) for 2 cases; hit the 25-step ceiling for 4 error cases |
| Latency | Not captured — local mode doesn't record `total_latency_s` (that field is only populated by `rca_builder`'s live/remote path telemetry). Known gap, noted below. |

## Two real findings from this baseline — read before trusting the raw pass/fail numbers

### Finding 1: the golden dataset's expected tool names are stale — this is why "tool selection" shows 0%

Every one of the 10 completed cases' `expected_trajectory` field in `agent/eval/golden_cases.py` uses tool names that **do not exist** in the current live tool set:

| Golden dataset says (stale) | Current real tool names |
|---|---|
| `list_pods`, `get_pod`, `describe_pod` | `list_k8s_events`, `describe_k8s_resource`, `get_k8s_resource` |
| `get_pod_logs`, `get_current_logs` | `get_k8s_logs` |
| `get_events`, `list_events` | `list_k8s_events` |

The agent's **actual** tool calls, checked by hand against each incident type, look correct: crashloop → `get_k8s_logs` + `list_k8s_events`; OOMKilled → `get_k8s_resource` + `get_k8s_logs` + `list_k8s_events`; ImagePullBackOff → `get_k8s_resource` + `list_k8s_events`. These are sensible, on-topic tool choices — they just can't score against a golden dataset that was written for an older tool-naming scheme. **This means today's 0% "trajectory match" number reflects test-data drift, not real tool-selection accuracy.** Fixing `golden_cases.py`'s `expected_trajectory` fields to the current tool names is real, contained follow-up work (not done here — no redesign was in scope for this task) that would immediately make this baseline meaningful for trajectory scoring specifically.

### Finding 2: local eval mode hits a lower recursion ceiling than the deployed agent

4 cases (`cascading-001`, `pending-001`, `conflicting-evidence-001`, `mcp-gateway-failure-001`) failed with:
```
Recursion limit of 25 reached without hitting a stop condition. You can increase the limit by setting the `recursion_limit` config key.
```
The deployed agent (`agent/main.py`'s `investigate()`) explicitly sets `recursion_limit=60` when invoking the graph. `agent/eval/run_eval.py`'s local mode does not pass this config, so it falls back to LangGraph's own default of 25. **These 4 cases may well complete fine against the actual deployed agent** — this is an eval-harness config gap, not a demonstrated production failure. Not fixed here (would be a code change to `run_eval.py`, out of scope for baseline capture) — flagged as a concrete, easy follow-up.

## What worked correctly, confirmed by direct evidence

- **Source selection**: 100% of completed cases correctly routed to `gke_remote_mcp` (the only viable source for a GKE-type cluster) — this is deterministic code, not model-guessed, and it worked every time.
- **Safe-stops worked exactly as designed** on the 2 cases built to test them:
  - `ambiguous-routing-001` (no cluster hint) → `outcome: insufficient_evidence`, 0 tool calls, confidence 0.0 — an exact match to the golden case's own `expected_outcome`/`max_confidence` fields, even though the blunt keyword scorer marked it "failed" (its `expected_keywords` didn't literally appear in the RCA text — another instance of Finding 1's class of problem, applied to keyword matching instead of trajectory matching).
  - `onprem-001` similarly safe-stopped rather than guessing at an unresolvable cluster.
- **Zero tool execution failures and zero routing failures** across all 10 completed cases — every real tool call that was attempted succeeded.
- **Contradiction detection worked live**: `imagepull-001`'s real run (see the earlier `invoke_agent.py` connectivity test) correctly flagged a genuine self-contradiction in the evidence (conflicting signals about whether the pod still existed) rather than picking a side — exactly the behavior the confidence-scoring design intends.

## Known gap in this baseline

**Latency was not captured.** `total_latency_s` is only populated by the live/remote path's telemetry (`rca_builder.py`), not local in-process mode. If latency needs to be part of the source-#3 comparison later, re-run using `--mode remote` against the deployed engine instead (slower and costs more per case, but captures real end-to-end latency) — not done here, in keeping with "no new infrastructure/large framework" for this baseline pass.

## How to re-run this exact baseline later (for the source-#3 comparison)

```bash
source scripts/init-env.sh
export CLUSTER_CONFIG_BUCKET=$(terraform -chdir=iac/agent output -raw cluster_config_bucket_name)
export EVAL_BUCKET=$(terraform -chdir=iac/agent output -raw eval_bucket_name)
python3 -m agent.eval.run_eval --mode local --cases all --output docs/baselines/tool-scaling-baseline-<date>-raw.json
```
Ensure the `k8s/*.yaml` fixtures are freshly applied to `test-incidents` first (see Risks & Gotchas in `NEXTSTEPS.md` item #7 — a self-healed fixture gives a false result).
