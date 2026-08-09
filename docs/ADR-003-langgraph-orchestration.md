# ADR-003: LangGraph as the orchestration framework

Status: Accepted

## Context

An SRE investigation agent needs to call an LLM repeatedly (plan the next check,
pick a tool, judge whether it has enough evidence, write the RCA) while looping
over live tool calls in between. A naive "LLM in a `while` loop calling tools
itself" design puts the *shape* of the workflow under the model's control — it
could, in principle, skip evidence-gathering and go straight to a root-cause
claim, or call a tool outside the intended flow, because nothing outside the
prompt enforces the sequence. For an agent that only ever gets read-only tools
but still has to be trusted to reason correctly about production incidents,
that is not an acceptable amount of freedom.

## Decision

Build the agent as a LangGraph state machine: 9 named nodes
(`input_normalizer`, `context_resolver`, `task_planner`, `mcp_router`,
`tool_executor`, `evidence_extractor`, `task_evaluator`, `loop_controller`,
`rca_builder`) wired by fixed/conditional edges defined in code
(`agent/graph.py:49-100`), sharing one `AgentState` object. The LLM is called
inside individual nodes (never more than once per node per graph step) to
decide *which conditional branch fires or which value to propose* — it never
gets to invent a new step or bypass the graph structure itself. `rca_builder`
is the only path to `END`, so every investigation — normal completion, a
safety-limit stop, or an early safe-stop — always ends with an RCA, never a
silent failure.

## Alternatives Considered

- **A hand-rolled agentic loop** (LLM calls a tool, sees the result, decides
  what's next, repeat, with no separate graph layer) — rejected because the
  *sequence* of steps (evidence before RCA, no skipping evaluation) would live
  only in prompt instructions, which an LLM can and does drift from under
  pressure or ambiguous input. The property we need — "the model cannot skip
  evidence-gathering" — has to be enforced by code structure, not prompt text.
- **A general agent framework with dynamic tool-calling loops** (e.g. letting
  the model freely decide how many tool calls to make and in what order,
  common in simple ReAct-style agents) — rejected for the same reason: it
  collapses planning, tool selection, and stopping into one LLM-controlled
  loop with no deterministic exit condition, which conflicts with the
  intentional deterministic `loop_controller` design (see [Investigation
  Loop](architecture/investigation-loop.md)).

## Reason

LangGraph's explicit graph — nodes and edges defined in code, one shared state
object every node reads/writes — makes the workflow's shape a compile-time
fact, not a runtime negotiation with the model. This is what lets safety
properties be stated as graph-structure facts instead of prompt hopes: "the
LLM cannot jump straight to an RCA without evidence" is true because
`rca_builder` is unreachable except through the loop or through the two
explicit safe-stop edges (`input_normalizer`/`context_resolver` on failure) —
see [LangGraph Workflow](architecture/langgraph-workflow.md).

## Tradeoffs

- The graph structure is the safety rail: the model can only influence which
  conditional branch fires, never invent a new step — directly enables
  evidence-before-RCA (ADR-006) and the deterministic loop-exit logic.
- Two independent step-count safety nets exist at different levels
  (LangGraph's own `recursion_limit=60`, a shared constant at
  `agent/graph.py:29`, and the real operative cap, `investigation.max_steps=5`
  enforced by `loop_controller`) — more moving parts to keep in sync, and they
  did drift apart once (`agent/main.py` hardcoded `60` separately from the
  eval harness, which silently used LangGraph's built-in default of 25,
  fixed 2026-08-09 and now guarded by
  `tests/test_recursion_limit_consistency.py`).
- Test coverage of the graph is uneven: only 3 of 9 nodes
  (`context_resolver`, `mcp_router`, `rca_builder`) have direct tests today;
  no test exercises the compiled graph end-to-end
  (`compile_graph()`/`graph.invoke()`) — a real, documented coverage gap, not
  a design flaw (see [Implemented vs Planned Matrix](management/implemented-vs-planned-matrix.md)).
- A dead/unused code path exists as a byproduct of this design evolving over
  time: `mcp_router`'s Phase 1 has no LLM call, but a leftover prompt pair for
  it still sits in `agent/prompts.py:53-69`, explicitly commented as unused.

## Related ADRs

- [ADR-006: Evidence before RCA](ADR-006-evidence-before-rca.md)
- [ADR-004: MCP as the tool-access protocol](ADR-004-mcp-tool-access-protocol.md)
