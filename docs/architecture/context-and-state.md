# Agent State and Context

> **Implementation Status:** IMPLEMENTED
> **Last Verified:** 2026-08-08 — `agent/state.py`
> **Source of Truth:** `agent/state.py:24-90` (the `AgentState` definition)
> **Owner:** SRE Agent platform team.

This is one of the most important pages for an Ops team new to AI agents. Read it slowly.

## What is "context"?

In everyday SRE language, "context" for an incident means: which cluster, which namespace, which pod, what's the error, has this happened before. For an AI agent, "context" means something more specific and more mechanical: **the exact text and data that gets sent to the language model on each call.** The model has no memory of its own between calls — every single LLM call in this system is a fresh request that includes only whatever text we explicitly construct and send. If something isn't in that constructed prompt, the model does not know it, no matter how "obviously relevant" it seems.

This is the single most important mental model to have about how this system works: **the model is stateless. Our code is what remembers things and decides what to tell it.**

## The different kinds of "state" and "context" in this system — don't conflate them

| Term | What it actually is | Where it lives |
|---|---|---|
| **User input** | The raw incident query text and any structured hints (namespace, pod, cluster) passed into `SREAgent.query()` | The `incident_envelope` dict, set once at investigation start |
| **PagerDuty incident context** | **PLANNED, not implemented.** No PagerDuty integration exists in the codebase today — `pagerduty_incident_id` is a permanent `None` placeholder in every log entry. See [Reliability](../governance/reliability.md) | n/a today |
| **LangGraph state (`AgentState`)** | The single shared data structure that flows through every node in the graph for one investigation — see the field reference below | In-memory, for the duration of one `graph.invoke()` call only |
| **LLM prompt context** | The specific text string sent to Gemini on any one call — built fresh by each node from whatever slice of `AgentState` (plus a prompt template) that node needs | Constructed per-call in `agent/prompts.py` + each node's own formatting; never persisted as-is |
| **MCP tool output** | The raw JSON/text a Kubernetes tool call returns | Never enters `AgentState` directly — written to GCS, then a compressed summary enters state (see below) |
| **Structured evidence** | The compressed, LLM-extracted facts about one piece of tool output | `evidence_store` in `AgentState`, keyed by `evidence_id` |
| **Session state** | Whatever Agent Engine's own session mechanism tracks if a caller passes a `session_id` | Managed by the platform, not application code — see [Agent Engine](agent-engine.md) |
| **Long-term memory** | Compressed summaries of *past* investigations, recalled into the *current* investigation's prompt context | Vertex AI Memory Bank (durable, cross-run) — see [Memory](memory.md) |

## How does the agent know which incident it's investigating?

Every investigation gets a fresh `run_id` (`agent/state.py:92-97`, format `run_{timestamp}_{4-char-suffix}`) and `incident_id` generated at the very start (`get_initial_state()`, `agent/state.py:100-139`). Every log line, every evidence record, and the final RCA all carry this `run_id` — it is the thread you pull to reconstruct one investigation end-to-end across every log source (see [Logging](../operations/logging.md)).

## Where does namespace / pod name / cluster / project / environment come from?

In order of precedence:
1. **Explicit fields on the incoming payload** (`namespace`, `pod`, `cluster`, `deployment`) — these are the most trusted.
2. **`resource_hints`** — a structured sub-object on the payload (used by `input_normalizer.py` to build verified `cluster_hint`/`namespace`/`project_hint`/`environment_hint` fields).
3. **LLM extraction from the free-text query** — `input_normalizer` also asks the model to guess a cluster name from the query text if nothing else is available. This becomes `cluster_guess` — explicitly marked **unverified**, and it is only ever consulted as a last resort, and only against a pre-approved alias list (see [Cluster Routing](cluster-routing.md), Tier 3).

**Defaults**: if `namespace` is entirely absent, it defaults to `"test-incidents"`; if `cluster` is absent, there is **no default cluster name substituted** — the agent goes through the full deterministic routing chain, and if that chain can't resolve a cluster, it safe-stops rather than guessing (`agent/nodes/context_resolver.py:57-75`).

## What happens when information is missing?

Depends on what's missing:
- **Missing query text entirely** → immediate safe-stop, no LLM call at all, straight to `rca_builder` with an explanatory error.
- **Missing/ambiguous cluster** → the 5-tier routing chain runs; if it can't resolve to exactly one cluster, safe-stop (same pattern) — see [Cluster Routing](cluster-routing.md).
- **Missing evidence during the loop** → `task_evaluator`'s hard gates force the loop to keep going (or, once max iterations are hit, `loop_controller` stops it anyway and `rca_builder` writes an RCA that says it couldn't determine a root cause).

The agent **never fabricates missing information** to keep going — every one of these paths either forces more investigation or an honest "I don't know."

## How does the agent discover missing information?

It doesn't proactively ask a human — this is a fully automated, non-interactive investigation loop. "Discovering" missing information means calling another tool (e.g., if the pod name is known but not its logs, the next loop iteration calls `get_pod_logs`). The `task_planner` node is what decides which gap to fill next, based on the current `evidence_store` contents.

## What information is passed to the model?

Each node constructs its own prompt from a narrow, explicit slice of state — never the whole `AgentState` object dumped wholesale. For example, `rca_builder`'s prompt includes: the original query, the incident type, the current working theory, a compressed evidence digest (not raw tool output), a memory-context string (if any relevant past incidents were recalled), and the resolved cluster/region/project. See [RCA Generation](rca-generation.md) for the exact prompt template fields.

## What information is intentionally NOT passed to the model?

- **Raw MCP tool output** — this never enters state at all, let alone a prompt. Only the LLM-extracted, compressed "key facts" from `evidence_extractor` do (see [Evidence Architecture](evidence-architecture.md)).
- **Full tool_history with every field** — nodes read from `tool_history` for logic (dedup checks, failure counting) but the *prompt text* sent to the model is a curated summary, not a dump of the whole list.
- **Other investigations' state** — see contamination prevention below.
- **Secrets, credentials, tokens** — redacted before evidence is even written to GCS, let alone before it could reach a prompt (see [Evidence Architecture](evidence-architecture.md#redaction)).

## How is context size controlled?

Three deliberate mechanisms:
1. **Per-node, per-call `max_tokens` output budgets** (300–1536 depending on the node — see the [LangGraph Workflow](langgraph-workflow.md) node table) bound how much the *model* can generate, which indirectly bounds downstream context growth.
2. **Evidence is compressed at write time** — `evidence_extractor` turns a potentially-large raw tool response into a handful of "key facts" strings before it ever becomes part of any future prompt.
3. **A digest, not a dump, feeds the RCA prompt** — `rca_builder`'s `_evidence_digest()` builds a bounded summary string from `evidence_store`, truncating individual raw-evidence re-reads to a head+tail budget (1000 + 2000 chars) when it does need to re-read full raw evidence from GCS for a thin investigation (`agent/nodes/rca_builder.py:54-102`).

## How are old tool results handled?

They stay in `tool_history` as compact records (tool name, ok/fail, duration, error if any — **never the raw output**), used by later nodes for logic (dedup, failure-pattern detection) but not blindly re-sent to the model as growing prompt text.

## How do we prevent context-window growth across a long investigation?

The `max_steps = 5` hard cap (see [Investigation Loop](investigation-loop.md)) is the primary control — this is a short investigation by design, not an open-ended agent loop. Within that bound, the compression mechanisms above keep any single prompt bounded regardless of how many tools have been called.

## How do we prevent one investigation from contaminating another?

Every investigation is a fresh `graph.invoke(get_initial_state(envelope), ...)` call (`agent/main.py:421-423,441-443`) — `AgentState` is created new, with no reference to any prior run's state. There is no shared mutable state object between concurrent or sequential investigations at the graph-state level. The only thing that legitimately crosses investigation boundaries is the long-term Memory Bank recall (explicitly, deliberately, and gated — see [Memory](memory.md)), plus the (best-effort, per-container-process) in-process fallback memory list, which is capped at 20 entries and is not durable.

---

## The complete `AgentState` reference

Defined in `agent/state.py:24-90`. Two reducer types matter for understanding how state updates work across nodes:
- **`operator.or_`** (dict merge) — a node's partial dict return is *merged into* the existing dict, not replacing it. This is why `loop_controller` updating only `current_step`/`status`/`loop_exit_reason` on `investigation` doesn't wipe out `max_steps` or other fields set earlier.
- **`_append`** (list concatenation, `agent/state.py:20-21`) — a node's returned list is appended to the existing list.

| Field | Type / reducer | Set by | Read by |
|---|---|---|---|
| `incident_envelope` | dict, set once | `get_initial_state` | most nodes |
| `run_id` | str | `get_initial_state` | almost every node (logging/GCS keys) |
| `incident_id` | str | `get_initial_state` | `rca_builder` observability log |
| `resolved_context` | dict, merge | `input_normalizer` (pre-resolution fields), `context_resolver` (routing fields) | most nodes |
| `investigation` | dict, merge | every node writes some subset | every node reads |
| `selected_mcp` | str, overwrite | `mcp_router` | `tool_executor` |
| `current_action` | dict, overwrite | `mcp_router` | `tool_executor`, routing logic |
| `latest_tool_result` | dict, overwrite | `tool_executor` sets it, `evidence_extractor` clears it | routing logic, `evidence_extractor` |
| `sources_skipped` | list, append | `mcp_router` | `rca_builder` (audit trail) |
| `evidence_ids` | list, append | `evidence_extractor` | most nodes |
| `evidence_store` | dict, merge | `evidence_extractor` — raw MCP output NEVER enters this, GCS only | most nodes |
| `evaluation_ids` | list, append | `task_evaluator` | `rca_builder` |
| `tool_history` | list, append | `tool_executor` — compact record only | `mcp_router`, `loop_controller`, `task_evaluator`, `rca_builder` |
| `errors` | list, append | most nodes | `main.py` (returned to caller), `rca_builder` (`status="error"` derivation) |
| `working_theory` | str, overwrite | `task_evaluator` | `task_planner`, `rca_builder` |
| `final_summary` | dict, overwrite | `rca_builder` only | `main.py` (this is the returned RCA) |

`investigation`'s notable sub-keys: `status`, `current_step`, `max_steps` (5), `min_steps` (2), `enough_evidence`, `confidence`/`confidence_band` (legacy, final value set only by `rca_builder`), `completeness` (deterministic score, set every `task_evaluator` call), `loop_exit_reason`, `tokens_input`/`tokens_output`/`tokens_total`/`estimated_cost_usd`, `started_at` (epoch seconds), `max_duration_seconds` (540).

---

**Related pages:** [LangGraph Workflow](langgraph-workflow.md) · [Evidence Architecture](evidence-architecture.md) · [Memory](memory.md) · [Investigation Loop](investigation-loop.md)
