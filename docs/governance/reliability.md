# Reliability and Failure Modes

> **Implementation Status:** Failure detection for most modes below is IMPLEMENTED; no formal SLOs are defined today (recommendations only).
> **Last Verified:** 2026-08-08
> **Owner:** SRE Agent platform team.

## Failure-mode matrix

| Failure | User impact | Detection | Automatic behavior | Operator action |
|---|---|---|---|---|
| Gemini model unavailable | Investigation fails or is delayed | `429`s auto-retried 3x; other errors propagate immediately | On non-429 exception: investigation returns `status:"failed"`, no partial RCA | See [Investigation-Level Failures](../runbooks/investigation-failure.md) |
| Agent Engine unavailable | No investigations can run at all | Platform-level, outside application observability | None — no fallback compute path | Check GCP status; escalate to platform team |
| Agent Gateway unavailable | Tool calls fail/hang | No dedicated gateway-health signal today (documented gap) | `fail_open=true` — an IAP-specific outage allows traffic through rather than blocking; a full gateway outage has no automatic mitigation | See [Gateway Failure runbook](../runbooks/gateway-failure.md) |
| MCP unavailable (either source) | Tool calls fail | `sre-agent-tool-failures` log + alerts | GKE Remote MCP failure auto-falls-back to custom MCP (currently non-functional live — see [MCP Architecture](../architecture/mcp-architecture.md)) | See [MCP Failure runbook](../runbooks/mcp-failure.md) |
| Cluster unavailable | Tool calls against it fail | Recorded as tool failures | Investigation continues with reduced evidence; loop-controller's `consecutive_tool_failures` check can end it early | See [Routing Failure runbook](../runbooks/routing-failure.md) |
| IAM failure | Specific tool calls `403` | Recorded as tool failures | Same as above | See [Identity Failure runbook](../runbooks/identity-failure.md) |
| Partial evidence | Lower-confidence RCA, possibly `escalate` band | Completeness score reflects it directly | RCA still produced, `requires_human_review=True` | Normal review process |
| GCS failure (evidence write) | That evidence item's audit trail is broken; investigation continues | `sre-agent-evidence-storage-failures` log + alert | 2 retry attempts before marking failed; not a hard stop | See [Data and Integration Failure runbook](../runbooks/data-and-integration-failure.md) |
| Memory failure | No recall context available for this run; no durable write of this run's outcome | No dedicated metric/alert (documented gap) | Falls back to in-process, non-durable memory | See [Data and Integration Failure runbook](../runbooks/data-and-integration-failure.md) |
| Routing failure (cluster or MCP-source) | Investigation safe-stops rather than guessing | Dedicated logs + alerts, both cases | RCA explains the failure explicitly, no fabricated cluster | See [Routing Failure runbook](../runbooks/routing-failure.md) |
| Malformed alert/payload | Missing-query safe-stop, or ambiguous-cluster safe-stop | Same safe-stop paths as above | Explicit RCA explaining what's missing | Fix upstream alert quality |
| Context-parsing failure | `input_normalizer` LLM call returns unusable output | `llm_json()` never raises, returns `{}` with a warning on unparseable output | Downstream fields default sensibly | Investigate the specific prompt/response if recurring |
| Evaluator failure | Loop may not converge on "enough evidence" correctly | `task_evaluator`'s two hard gates bound the risk (forces continue rather than a false-positive stop) | Loop continues, eventually hits `max_steps` if evidence genuinely insufficient | See [Investigation-Level Failures](../runbooks/investigation-failure.md) |
| Runaway loop | Longer investigation, higher cost | `loop_controller`'s 9 deterministic exit checks (see [Investigation Loop](../architecture/investigation-loop.md)) | Hard-bounded by `max_steps=5`, `max_duration_seconds=540`, `MAX_TOKENS_PER_RUN=100,000`, LangGraph `recursion_limit=60` | Review the specific `loop_exit_reason` |
| Token limit hit | Investigation stops early | `token_budget_exceeded` exit reason | RCA produced with whatever evidence exists | Review if this incident type genuinely needs more budget |
| Timeout | Investigation stops at 540s | `timeout` exit reason | RCA produced with whatever evidence exists | Check traces for which node was slow |

## SLOs / SLIs

**No formal SLOs are currently defined or committed to** for this system — the alert thresholds in [Alerting](../operations/alerting.md) are operational tripwires, not published service-level commitments. Below are **recommended** SLIs based on what's actually measurable today — clearly distinguished from anything currently committed:

| Recommended SLI | Measurable from | Status |
|---|---|---|
| Investigation availability (successful completion rate, excluding intentional safe-stops) | `sre_agent/invocations` vs `sre_agent/errors` | RECOMMENDED, not committed |
| Successful investigation rate (`auto`/`review` vs `escalate`) | `sre_agent/confidence_band` | RECOMMENDED |
| Median / P95 investigation latency | `sre_agent/investigation_latency_seconds` | RECOMMENDED (metric already exists) |
| MCP tool success rate | `sre_agent/tool_failures` vs total tool calls (total isn't currently a standalone metric — would need to be added) | RECOMMENDED, partially measurable today |
| Routing accuracy | `sre_agent/routing_failures` + `sre_agent/unresolved_cluster`, inverted | RECOMMENDED |
| Evidence completeness | `investigation_completeness` score distribution (not currently exported as its own metric, only present in per-run logs) | RECOMMENDED, needs a new metric |
| RCA generation success | `rca_builder` always runs — this would really measure "did it produce a non-degenerate RCA," which needs a new signal | RECOMMENDED, not currently measurable |
| Evaluation quality (golden-case pass rate over time) | `agent/eval/run_eval.py` output, not currently persisted as a trend | RECOMMENDED, needs a tracking mechanism |
| Confidence calibration (does `auto`-band correctness actually match ~85%+?) | Requires the closed feedback loop noted as PLANNED in [Evaluation](../architecture/evaluation.md) | RECOMMENDED, blocked on that gap |

**Recommendation before publishing any SLO externally**: given the confidence-scoring policy is explicitly self-labeled "uncalibrated," treat any reliability commitment tied to confidence bands as provisional until real calibration work happens.

---

**Related pages:** [Alerting](../operations/alerting.md) · [Capacity and Quotas](capacity.md) · [Scaling](scaling.md)
