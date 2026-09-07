# Vertex AI Agent Engine

> **Implementation Status:** IMPLEMENTED
> **Last Verified:** 2026-08-08 — `iac/agent/agent_engine.tf`, `agent/main.py`, `iac/agent/terraform.tfvars`
> **Source of Truth:** `iac/agent/agent_engine.tf:87-144` (the deployed resource), `agent/main.py:1-60`
> **Owner:** SRE Agent platform team.

## What it is

Vertex AI **Agent Engine** (Google calls the underlying resource type a "Reasoning Engine") is a managed, serverless runtime purpose-built for hosting AI agents. You give it your agent's Python code and an entrypoint class; Google runs it as a container, scales it, and exposes a `query()`-style API. Conceptually it's similar to Cloud Run, but with agent-specific plumbing built in (session management, built-in tracing hooks, a companion Memory Bank feature, and — the reason we use it — Agent Identity and Agent Gateway integration).

## Why we use it

We wanted the agent's Kubernetes access to be governed the same way any other GCP-to-GCP service call is governed: via IAM, with a real per-workload identity, no static credentials, and centrally auditable egress. Agent Engine is the GCP product that provides that (via Agent Identity, see [Agent Identity](agent-identity.md)) for an agentic workload specifically, rather than us building an equivalent on GKE/Cloud Run ourselves.

## What exactly is deployed there

One resource: `google_vertex_ai_reasoning_engine.sre_agent` (`iac/agent/agent_engine.tf:87-144`). Its `display_name` is `sre-agent-gcp`. The entrypoint is `agent.main.SREAgent` (`entrypoint_module = "agent.main"`, `entrypoint_object = "SREAgent"`, Python 3.11 — `iac/agent/agent_engine.tf:106-108`).

A **second, separate** Reasoning Engine resource exists purely as durable storage — `google_vertex_ai_reasoning_engine.memory_bank` (`iac/agent/agent_engine.tf:21-34`), display name `sre-agent-memory-bank`. It has no source code of its own; it's used only via Vertex AI's Memory Bank API. See [Memory](memory.md).

### How the code gets there

There is **no GCS staging bucket** for source code. The entire `agent/` directory is packaged into a `.tar.gz` archive (built reproducibly by `scripts/package_agent.py` — sorted entries, fixed mtimes, no `__pycache__`/`.env`), base64-encoded, and embedded directly in the Terraform resource body via `filebase64("../../agent.tar.gz")` (`iac/agent/agent_engine.tf:100-104`). Every `terraform apply` that touches this resource re-submits the full source archive, even for a config-only change.

## What lifecycle Agent Engine manages

- **Compute**: provisions and scales container instances running your code. You don't manage VMs, nodes, or a container orchestrator.
- **The `query()` API surface**: Agent Engine calls `SREAgent.set_up()` once when a container starts (`agent/main.py:623-662` — this is where the LangGraph graph is pre-compiled and the Model Armor/Memory Bank clients are initialized), then calls `SREAgent.query(**kwargs)` for every request.
- **Session handling**: if a caller passes a `session_id`, Agent Engine's session mechanism is what makes that meaningful (`agent/main.py:976-977` just echoes it back on the response).

## What configuration we control

Everything in `iac/agent/agent_engine.tf`: the identity type, the entrypoint, resource limits, minimum instance count, and every environment variable the agent code reads at runtime (`local.agent_env`, `iac/agent/agent_engine.tf:37-84` — this is where `PROJECT_ID`, `REGION`, `GEMINI_MODEL`, bucket names, and the Memory Bank resource path all get set).

## What configuration Google manages

- Container scheduling and autoscaling internals.
- The Agent Identity SPIFFE credential issuance mechanism itself (we only reference the resulting principal string in IAM bindings — see [Agent Identity](agent-identity.md)).
- The underlying compute platform patching/security.

## How the agent is invoked

`SREAgent.query(**kwargs)` (`agent/main.py:872-979`) is the real entrypoint Agent Engine calls. It:
1. Optionally parses a `prompt` JSON key (used by Vertex AI's `evals.run_inference()` tooling).
2. Runs Model Armor input sanitization — **but only if `MODEL_ARMOR_TEMPLATE` is set**, which, per `iac/agent/agent_engine.tf:76-78`, is only set when the Agent Gateway is **disabled**. The live deployment has the gateway **enabled** (`enable_agent_gateway = true`, `iac/agent/terraform.tfvars:9`), so this app-level sanitize step does not currently run. The gateway's own Model Armor `CONTENT_AUTHZ` extension is wired and live instead (`iac/agent/agent_gateway.tf`) — but it can only inspect the request/response bodies Google's platform actually invokes it for, which for Streamable HTTP MCP tool traffic does not include the MCP tool response body. That gap is a permanent Google-platform limitation, not a configuration issue, and is compensated for on the custom MCP path only by an application-level response guard (`mcp/response_guard.py`). See [Security Operations](../governance/security.md#model-armor) and [Agent Gateway](agent-gateway.md) for the full picture.
3. Recalls relevant memory (see [Memory](memory.md)).
4. Calls the module-level `investigate(payload)` function, which runs the actual LangGraph workflow.
5. Persists the RCA to GCS, conditionally writes to Memory Bank, and runs output-side Model Armor sanitize (same caveat as step 2).

## How it scales

`min_instances = 2` (`iac/agent/agent_engine.tf:113-116`) — two warm instances are kept running to avoid cold-start latency on the first request. **`max_instances` is not set anywhere in this repo's Terraform** — status: **UNKNOWN**, the platform default applies and is not visible from this codebase; verify directly in the GCP Console or via `gcloud` if you need this number for capacity planning.

`resource_limits`: `cpu = "4"`, `memory = "8Gi"` per instance (`iac/agent/agent_engine.tf:117`).

## What happens during concurrent investigations

Each `query()` call is handled independently — state (`AgentState`, see [Context and State](context-and-state.md)) is scoped to a single LangGraph `invoke()` call and is never shared between concurrent requests. Module-level globals in `agent/gemini_client.py` (session token counters) and the in-process memory fallback list in `agent/main.py` are per-container-process, not per-request — under concurrent load on the same container instance, these could interleave. This has not been independently load-tested in this pass; flag as **UNKNOWN** if you need a hard concurrency guarantee.

## What happens if Agent Engine fails

If the platform itself is unavailable, requests fail at the caller (whatever is invoking `query()`) — there's no fallback compute path. Within a single investigation, if an unhandled exception occurs anywhere in `investigate()`, the outer `try/except` catches it and returns `{"error": str(exc), "status": "failed"}` (`agent/main.py:598-600`) rather than crashing the container — no partial results are returned on an internal crash.

## How a new agent version is deployed

There is no separate "version" resource — a new deployment is a `terraform apply` that re-submits the packaged source archive against the **same** `google_vertex_ai_reasoning_engine.sre_agent` resource (in-place update). See [Updating the Agent](../operations/deployment.md) and [CI/CD](../operations/deployment.md#cicd) for the exact pipeline.

## How we roll back

**Status: UNKNOWN / no dedicated mechanism.** There is no blue/green or versioned-resource rollback built into this Terraform. A rollback today means re-running the deployment pipeline against an older git commit (which re-packages and re-applies that older source). See [Rollback](../operations/rollback.md) for the exact manual procedure and its gaps.

---

**Related pages:** [Agent Identity](agent-identity.md) · [LangGraph Workflow](langgraph-workflow.md) · [Updating the Agent](../operations/deployment.md)
