# Glossary

> **Owner:** SRE Agent platform team. Terms are explained in plain language first, with a link to the deeper page.

**LLM (Large Language Model)** — the underlying AI model (here, Google's Gemini) that reads text and generates text in response. It has no memory between calls — every call is fresh. See [Context and State](../architecture/context-and-state.md).

**AI Agent** — software that uses an LLM to make decisions and take actions (here: which tool to call next) within a bounded, code-defined workflow, rather than just answering a single question.

**Agent Engine** — the Google Cloud managed platform this agent's code actually runs on. See [Agent Engine](../architecture/agent-engine.md).

**Agent Identity** — the platform-issued, no-static-key identity this specific deployment uses to authenticate to other GCP services. See [Agent Identity](../architecture/agent-identity.md).

**Agent Gateway** — the centralized egress-authorization layer all of the agent's outbound calls go through. See [Agent Gateway](../architecture/agent-gateway.md).

**LangGraph** — the library used to define the agent's workflow as an explicit set of steps ("nodes") and paths between them ("edges"), so the AI model can't skip or reorder the safety-relevant structure. See [LangGraph Workflow](../architecture/langgraph-workflow.md).

**Node** — one step in the LangGraph workflow (e.g., `task_planner`, `tool_executor`). See [LangGraph Workflow](../architecture/langgraph-workflow.md).

**State** (`AgentState`) — the one shared data structure that flows through every node during a single investigation. See [Context and State](../architecture/context-and-state.md).

**Context** — the specific text/data actually sent to the LLM on any one call. See [Context and State](../architecture/context-and-state.md).

**Context window** — the maximum amount of text an LLM can process in a single call. This system deliberately keeps prompts small via evidence compression, rather than relying on a large context window.

**Prompt** — the text template + data sent to the LLM for one specific decision (e.g., the RCA-building prompt). See `agent/prompts.py`.

**Tool** — one specific, named action the agent can request (e.g., `list_k8s_events`) — always read-only in this system. See [Tool Selection](../architecture/tool-selection.md).

**Tool call** — one instance of the agent invoking a tool with specific arguments.

**MCP (Model Context Protocol)** — the open standard defining how an AI agent calls external tools via a client/server pattern. See [MCP Architecture](../architecture/mcp-architecture.md).

**MCP client** — the agent's own code that sends tool-call requests (`agent/mcp_client.py`).

**MCP server** — a service exposing a set of tools an MCP client can call (e.g., GKE Remote MCP, our custom MCP).

**MCP tool** — one named capability an MCP server exposes.

**Evidence** — a compressed, LLM-extracted summary of one piece of tool output, stored in `evidence_store` and referenced by an `evidence_id`. Distinct from the raw tool output, which is only ever stored in GCS. See [Evidence Architecture](../architecture/evidence-architecture.md).

**Evaluator** — `task_evaluator`, the node that decides (with two hard code gates plus an LLM judgment) whether enough evidence exists to stop investigating.

**Confidence** — a deterministic, code-computed score reflecting how well-supported the proposed root cause is by real evidence — not the model's self-reported certainty. See [Confidence Scoring](../architecture/confidence.md).

**Investigation completeness** — one of the two confidence axes: did the investigation collect what this incident type needs? See [Confidence Scoring](../architecture/confidence.md).

**Root cause confidence** — the other axis: does the evidence actually support the proposed cause? See [Confidence Scoring](../architecture/confidence.md).

**Memory** — compressed summaries of past, high-confidence investigations, recalled into future prompts on the same cluster/namespace. See [Memory](../architecture/memory.md).

**Embedding / vector search** — **not currently used anywhere in this system.** Memory recall uses Vertex AI Memory Bank's own retrieval mechanism, not a custom vector-search implementation in this codebase.

**Agent Engine session** — Agent Engine's own optional session-tracking mechanism, used only if a caller passes a `session_id`; not the same thing as `AgentState`.

**GKE Remote MCP** — Google's own managed MCP server for GKE cluster access. See [MCP Architecture](../architecture/mcp-architecture.md).

**Connect Gateway** — a GCP product for reaching non-GKE/on-prem Kubernetes clusters via a fleet-registered, short-lived-credential mechanism. Proven manually in this repo; not wired into the production agent yet. See [GKE vs Non-GKE Access](../architecture/gke-vs-nongke.md).

**OpenTelemetry (OTel)** — the open standard used here for tracing (`agent/otel.py`).

**Trace** — the record of one investigation's full execution timeline across every LangGraph node, viewable in Cloud Trace. See [Tracing](../operations/tracing.md).

**Span** — one segment of a trace (e.g., one LangGraph node's execution).

**Token** — the unit LLMs process text in; roughly a word-fragment. Token counts directly drive both cost and the `MAX_TOKENS_PER_RUN` safety limit. See [Investigation Loop](../architecture/investigation-loop.md).

**Hallucination** — an LLM stating something confidently that isn't actually true/supported. This system's defense is the deterministic evidence-grounding check in [Confidence Scoring](../architecture/confidence.md) — a claim citing evidence that doesn't exist, or that doesn't actually support it, is caught by code, not trusted.

**Prompt injection** — malicious content (e.g., in a log line the agent reads) crafted to manipulate the LLM into unintended behavior. This system's primary defense is that there's no mutating action available for an injected instruction to trigger, regardless of whether the model is "convinced." See [Security Operations](../governance/security.md).

**AI evaluation** — testing whether the agent's outputs are correct, using golden test cases and deterministic scoring. See [Evaluation](../architecture/evaluation.md).

**Judge model** — an LLM used to grade another LLM's output. **Does not exist anywhere in this system today** — all evaluation is deterministic string/set matching. See [Evaluation](../architecture/evaluation.md).

**Golden dataset** — the curated set of test scenarios (14 today) with known-correct expected behavior, used for evaluation. See [Evaluation](../architecture/evaluation.md).

---

**Related pages:** [System Overview](../architecture/system-overview.md) · [Run-Team Onboarding Guide](run-team-guide.md)
