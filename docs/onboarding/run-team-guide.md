# New Run-Team Onboarding Guide

> **Owner:** SRE Agent platform team.

A 5-day path for a new engineer with normal cloud/SRE background and zero AI-agent experience.

## Day 1 — Understand the architecture

- Read [System Overview](../architecture/system-overview.md) first, all the way through.
- Read [Glossary](glossary.md) alongside it — don't skip terms you don't recognize.
- Read [LangGraph Workflow](../architecture/langgraph-workflow.md) and [Investigation Loop](../architecture/investigation-loop.md) — understand the node-by-node flow and exactly what stops the loop.
- **Lab**: draw the graph flow from memory (pen and paper is fine), then check it against the [LangGraph Workflow](../architecture/langgraph-workflow.md) diagram. Note what you got wrong.

## Day 2 — Follow one investigation end-to-end

- Read [Context and State](../architecture/context-and-state.md), [Evidence Architecture](../architecture/evidence-architecture.md), [Confidence Scoring](../architecture/confidence.md), [RCA Generation](../architecture/rca-generation.md).
- **Lab**: run a real investigation —
  ```bash
  python3 invoke_agent.py --scenario imagepull
  ```
  Then, using only the `run_id` from the output, manually reconstruct the full picture using [Logs](../operations/logging.md)'s query sequence — find the `sre-agent-investigations` entry, the evidence records, the confidence breakdown. Don't just read the final RCA text; find the raw evidence behind each claim in it.

## Day 3 — Learn logs, metrics, and traces

- Read [Observability](../operations/observability.md), [Logging](../operations/logging.md), [Tracing](../operations/tracing.md), [Alerting](../operations/alerting.md).
- **Lab**: pick a `run_id` from Day 2's lab, find its `trace_id`, open it in Cloud Trace, and identify which node took the longest. Cross-reference with `node_token_usage` log events for the same run.
- **Lab**: run through the full [Daily Health Check](../operations/daily-health-check.md) checklist for real, against the live system.

## Day 4 — Troubleshoot intentional failure scenarios

- Read the [Troubleshooting Runbooks](../runbooks/) directory in full.
- **Lab (safe, reversible)**: deliberately trigger a few real failure modes and confirm you can diagnose them using the runbooks, without needing to ask anyone:
  - Send a payload with no `query` field — confirm you can find the resulting safe-stop in the logs and explain why it happened, referencing [Context and State](../architecture/context-and-state.md).
  - Send a payload with a nonsense/unregistered cluster name — confirm you can trace it through the 5-tier routing chain to `unresolved` (see [Cluster Routing](../architecture/cluster-routing.md)) and find the corresponding alert.
  - Review — don't trigger, just read — the [Gateway Failure](../runbooks/gateway-failure.md) and [MCP Failure](../runbooks/mcp-failure.md) runbooks, and identify which failure modes are currently *real, live-possible* risks (per [Risks and Limitations](../management/risks-and-limitations.md)) vs. which describe infrastructure that isn't deployed at all (e.g., the custom MCP failure alert).

## Day 5 — Perform a safe agent/MCP deployment in development

- Read [Updating the Agent / CI/CD](../operations/deployment.md), [Terraform / Infrastructure Management](../operations/terraform.md), [Rollback](../operations/rollback.md).
- **Lab**: make a trivial, reversible change (e.g., a log message tweak in a node), open a PR, watch `terraform-plan.yml` run and post its plan comment, get it reviewed, merge, and watch `terraform-apply.yml` run end-to-end — including the self-healing gateway-attach step and the final smoke test. Then read [Rollback](../operations/rollback.md) and (in a genuinely safe dev context, not live production) practice the rollback procedure.

## Before you're considered fully onboarded

You should be able to, without help:
- Explain the difference between `AgentState`, evidence, and long-term memory.
- Explain why the agent can never delete a pod, citing the specific enforcement layers.
- Find the full log trail for any given `run_id`.
- Name at least 3 things this system's own documentation says are *not* working today, and why that matters (see [Risks and Limitations](../management/risks-and-limitations.md)) — a good Run-team member knows the gaps as well as the working parts.

---

**Related pages:** [Glossary](glossary.md) · [System Overview](../architecture/system-overview.md) · [Troubleshooting Runbooks](../runbooks/)
