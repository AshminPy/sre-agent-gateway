# Tool Selection

> **Implementation Status:** IMPLEMENTED
> **Last Verified:** 2026-08-08 — `agent/mcp_client.py`, `agent/nodes/mcp_router.py`
> **Source of Truth:** `agent/mcp_client.py:30-95`
> **Owner:** SRE Agent platform team.

## What tools currently exist

Two fixed allowlists, defined in code — not discovered dynamically from the model's perspective:

- **`GKE_REMOTE_TOOLS`** (6 tools, `agent/mcp_client.py:30-37`): `list_k8s_events`, `describe_k8s_resource`, `get_k8s_resource`, `get_k8s_logs`, `list_k8s_api_resources`, `get_k8s_cluster_info`.
- **`CUSTOM_K8S_TOOLS`** (27 tools, `agent/mcp_client.py:40-87`): read-only tools across pods, deployments, replicasets, statefulsets, daemonsets, services, endpoints, nodes, configmaps, HPAs, PVCs, namespace events, jobs.

Every tool name in both lists is a `list_*`/`get_*`/`describe_*` verb. There is no secrets-reading tool in either list, and a hard-coded blocklist (`BLOCKED_ACTIONS`, `agent/mcp_client.py:92-95`: `delete`, `create`, `patch`, `update`, `apply`, `exec`, `port-forward`, `scale`, `rollout`) is checked against every proposed tool name regardless of allowlist membership, before any network call happens (`agent/mcp_client.py:244-251`).

## Where tool descriptions are defined

Inline in the allowlist dicts themselves (`agent/mcp_client.py:30-87`) — each entry has a short description string that gets included in the Phase 2 prompt sent to the model.

## Does the agent discover tools dynamically?

**No — not from the agent's own runtime perspective.** The two allowlists above are static Python data structures, hardcoded in `agent/mcp_client.py`. The model is shown the (already-filtered-to-the-selected-source) tool list as part of its prompt; it does not query a live tool-discovery endpoint at investigation time.

## Does Agent Gateway expose tools dynamically?

Separately, yes — Agent Gateway's Agent Registry does support dynamic tool discovery as a platform feature (this is how MCP servers get registered — see [MCP Architecture](mcp-architecture.md#agent-registry)), but that's a *registration-time* mechanism used by our deployment scripts, not something the running agent queries per-investigation. The agent code always uses its own static allowlists.

## Does the agent receive the full MCP tool list?

No. `mcp_router`'s Phase 1 first deterministically picks the MCP *source* (GKE Remote MCP or the custom MCP), and only Phase 2's prompt shows the model the allowlist for *that already-chosen source* — never both lists combined, and never the raw MCP server's full tool manifest.

## How does the LLM understand what each tool does?

Purely from the short description strings baked into the allowlist entries, included in the Phase 2 prompt (`MCP_ROUTER_PHASE2_SYSTEM/USER`, `agent/prompts.py`). There is no tool-schema introspection at call time beyond this.

## How does it choose between tools?

The model picks one tool name + arguments from the shown allowlist, based on the current `task_plan`/`primary_gap` (set by `task_planner`) and what's already in `evidence_store`. This is a single structured-JSON LLM call (`agent/nodes/mcp_router.py:142-163`) — not a multi-turn tool-use conversation.

## What prevents an invalid tool call?

The model's chosen tool is validated against `phase2_allowed` (the allowlist for the already-locked-in source) **before execution** (`agent/nodes/mcp_router.py:187-193`). If it's not in the list, `mcp_router` safe-stops (`current_action={"tool":"done"}`) rather than executing an unvalidated call. This is enforced by code, not by trusting the model's output.

## What happens when a requested tool is unavailable?

Same mechanism as above — if the model names a tool outside the allowlist (effectively "unavailable" from this system's perspective), the call is never made; the router safe-stops instead.

## What happens if tool schema changes?

**STATUS: manual process, no automated compatibility check.** If a tool's arguments or return shape changes upstream (e.g., a Google API change to GKE Remote MCP, or a code change to the custom MCP's `mcp/server.py`), nothing in this repo automatically detects a mismatch — you'd find out via a tool-call failure logged to `sre-agent-tool-failures`, or a golden-eval-case failure if the change affects a tested scenario. See [Updating Existing MCP Tools](../runbooks/update-mcp-tool.md).

## What happens if a tool returns malformed output?

`evidence_extractor`'s LLM extraction step (`agent/nodes/evidence_extractor.py`) is what parses tool output into structured facts; `agent/gemini_client.py`'s `llm_json()` has fallback JSON-repair logic and never raises on unparseable model output — it returns `{}` with a warning instead. A truly malformed *tool* response (not model output) would most likely surface as a tool-call error caught by `tool_executor`'s own error handling, logged and recorded as a failed call rather than crashing the investigation.

## What determines whether another tool should be called?

`task_evaluator`'s `enough_evidence` decision (gated by the two hard code checks described in [Investigation Loop](investigation-loop.md)) — if not enough, `loop_controller` routes back to `task_planner`, which picks the next gap, and the cycle repeats.

---

**Related pages:** [LangGraph Workflow](langgraph-workflow.md) · [MCP Architecture](mcp-architecture.md) · [Dynamic MCP Routing](dynamic-mcp-routing.md)
