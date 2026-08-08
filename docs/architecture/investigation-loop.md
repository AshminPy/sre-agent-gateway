# Agent Reasoning and Investigation Loop

> **Implementation Status:** IMPLEMENTED
> **Last Verified:** 2026-08-08 — `agent/nodes/loop_controller.py`, `agent/state.py`
> **Source of Truth:** `agent/nodes/loop_controller.py:97-179`
> **Owner:** SRE Agent platform team.

## The loop, in order

```
task_planner  →  mcp_router  →  tool_executor  →  evidence_extractor  →  task_evaluator  →  loop_controller
      ^                                                                                            |
      └────────────────────────────── loop back if not done ─────────────────────────────────────┘
                                                                                                     |
                                                                                                  rca_builder (done)
```

## Who decides what

This is the question every new person asks, and the answer is precise, not fuzzy:

| Decision | Decided by | Where |
|---|---|---|
| Which specific fact/gap to investigate next | LLM | `task_planner` |
| Which MCP *source* to use (GKE Remote MCP vs. custom MCP) | **Code, deterministic** | `mcp_router` Phase 1 — see [Dynamic MCP Routing](dynamic-mcp-routing.md) |
| Which *tool* (within the already-chosen source's allowlist) to call, and its arguments | LLM, but hard-validated against the allowlist by code before execution | `mcp_router` Phase 2 |
| Whether enough evidence exists to stop | LLM proposes; two hard code gates can override it to "not enough" before the LLM is even asked | `task_evaluator` |
| Whether to actually stop the loop (final decision) | **Code, deterministic, no LLM involved at all** | `loop_controller` |
| The final root cause / RCA text | LLM proposes | `rca_builder` |
| The final confidence score and outcome | **Code, deterministic** — the LLM's claims are checked and scored by code, not trusted at face value | `rca_builder` via `agent/confidence/` — see [Confidence Scoring](confidence.md) |

**The rule of thumb**: the LLM proposes *what to investigate* and *what it thinks happened*; deterministic code decides *when to stop*, *what's allowed*, and *how much to trust what the LLM said*.

## What LangGraph itself enforces

The *shape* of the graph — which node can follow which — is fixed in `agent/graph.py` and cannot be altered at runtime by the model. No matter what the LLM says in any prompt, it cannot cause the graph to call a node out of order, skip the evidence-extraction step, or invoke a tool that isn't in the allowlist mcp_router validated.

## What the model can NOT control

- It cannot pick the MCP source.
- It cannot call a tool outside the pre-validated allowlist for that source.
- It cannot set its own confidence score (the legacy field is overwritten by deterministic scoring at the end).
- It cannot decide to skip the loop-exit safety checks.
- It cannot extend `max_steps`, `max_duration_seconds`, or the token budget.

## What stops the loop — exact limits and every exit reason

All numeric limits come from `get_initial_state()` in `agent/state.py:100-139` unless noted.

| Limit | Value | Configurable? |
|---|---|---|
| `max_steps` (maximum loop iterations) | **5** | No — hardcoded, no env var |
| `min_steps` (minimum iterations before the loop is allowed to stop early) | **2** | No — hardcoded |
| `max_duration_seconds` (wall-clock timeout) | **540** (9 minutes) | No — hardcoded |
| `MAX_TOKENS_PER_RUN` | **100,000** (0 disables the check) | **Yes** — the one limit that is env-var configurable |
| LangGraph `recursion_limit` | **60** | Hardcoded in `agent/main.py:423,443` — a separate, much larger safety net, not the operative limit |

`loop_controller.py:97-179` checks these conditions **in this exact priority order** every iteration — first match wins:

| Priority | `loop_exit_reason` | Exact trigger |
|---|---|---|
| 1 | `confidence_sufficient` | `task_evaluator` set `enough_evidence=True` |
| 2 | `timeout` | Wall-clock time since `started_at` exceeds 540s |
| 3 | `token_budget_exceeded` | Cumulative `investigation.tokens_total` exceeds 100,000 |
| 4 | `max_iterations` | `current_step >= 5` |
| 5 | `tool_signaled_done` | `mcp_router` returned `current_action.tool == "done"` AND at least 2 steps have run |
| — (continue) | — | Router said done, but fewer than 2 steps have run — forced to keep going |
| 6 | `consecutive_tool_failures` | The last 2 tool calls both failed, and at least 2 steps have run |
| 7 | `oscillation_detected` | The last 4 successful tool calls form an A→B→A→B pattern |
| 8 | `stuck_detected` | The exact same tool + arguments succeeded twice in a row |
| 9 | `zero_new_facts` | The last 2 successful evidence entries both had empty `key_facts` |
| — (keep running) | `None` | None of the above matched |

Six of these reasons (`stuck_detected`, `zero_new_facts`, `oscillation_detected`, `timeout`, `consecutive_tool_failures`, `token_budget_exceeded`) also append an entry to `errors`. If `max_steps` is hit and evidence was never deemed sufficient, an additional error is appended.

## Failed MCP call behavior

A single failed tool call does not stop the investigation. It's recorded in `tool_history` (compact — tool name, error, no raw output), logged to a dedicated `sre-agent-tool-failures` Cloud Logging entry, and the loop continues — `task_planner` will typically try something else next iteration. Two failures **in a row** (after the minimum-steps floor) does trigger a stop (`consecutive_tool_failures`, priority 6 above).

## Retry logic

There is no automatic retry of a failed tool call within a single loop iteration — a failure is recorded and the loop moves on to decide its next step, rather than blindly re-attempting the same call. (Retry logic *does* exist at a different layer — Gemini API `429` rate-limit errors get up to 3 retries with backoff inside `agent/gemini_client.py`, and GCS evidence writes get 2 attempts — but neither of those is "MCP tool call retry.")

## Missing-tool behavior

If `mcp_router`'s LLM call proposes a tool that isn't in the currently-selected source's allowlist, the tool is never executed — `mcp_router` safe-stops to `current_action={"tool":"done"}` instead. See [Tool Selection](tool-selection.md).

## Partial-investigation behavior

If the loop stops for any reason other than `confidence_sufficient` — timeout, budget, max iterations, stuck/oscillating, zero new facts — `rca_builder` still runs and still produces an RCA, but with whatever evidence was collected up to that point. If zero evidence was ever collected, `rca_builder` hard-overrides the LLM's proposed root cause with an explicit "no evidence was extracted" statement rather than letting the model guess (`agent/nodes/rca_builder.py:373-384`).

---

## Worked example: OOMKilled pod

This walks through a realistic run using the actual node names and decision points above (illustrative — exact tool names/order depend on what the model decides, within the allowlist).

1. **`input_normalizer`**: parses "Pod payment-worker-7f9 is OOMKilled, investigate" → `incident_type="OOMKilled"`, `pod="payment-worker-7f9"`.
2. **`context_resolver`**: resolves the cluster deterministically (see [Cluster Routing](cluster-routing.md)), sets `primary_mcp_source="gke_remote_mcp"`.
3. **Iteration 1** — `task_planner` decides "check current pod status first." `mcp_router` Phase 1 picks `gke_remote_mcp` (deterministic), Phase 2 picks `get_k8s_resource` for the pod. `tool_executor` calls it. `evidence_extractor` writes the raw status to GCS and extracts key facts ("container payment-worker OOMKilled, restart count 4"). `task_evaluator`: not enough evidence yet (only 1 step, below `min_steps=2`). `loop_controller`: continue.
4. **Iteration 2** — `task_planner` decides "check recent events and logs for OOM signal." `mcp_router` picks `list_k8s_events` (or the custom-MCP equivalent). Evidence extracted: "OOMKilled at 14:32, memory limit 512Mi exceeded." `task_evaluator`: LLM proposes evidence may be sufficient. `loop_controller`: `enough_evidence=True` → exit reason `confidence_sufficient`.
5. **`rca_builder`**: LLM proposes root cause ("container exceeded its 512Mi memory limit"), builds claims/hypotheses, code scores confidence deterministically (see [Confidence Scoring](confidence.md)) — likely lands in `auto` or `review` band given two independent, direct pieces of supporting evidence and no contradictions. RCA written, evidence stored, logged.

Total: 2 loop iterations, well under the 5-iteration cap and the 9-minute timeout.

---

**Related pages:** [LangGraph Workflow](langgraph-workflow.md) · [Confidence Scoring](confidence.md) · [Dynamic MCP Routing](dynamic-mcp-routing.md)
