# Runbook: Operate (Day-2 Operations)

> **Last Verified:** 2026-09-08 (Section 11 of the new-assignment expansion work) · **Owner:** SRE Agent platform team

One coherent entry point for the operational tasks that were previously scattered across `docs/operations/*.md` with no single page tying them together — this runbook is that page. Each section below is real and immediately usable; deeper detail links to the dedicated doc for that topic.

## 1. Invoke the agent

```python
from agent.main import SREAgent
result = SREAgent.query(
    query="Pod checkout-abc123 in prod is CrashLoopBackOff",
    cluster="sre-test-cluster", namespace="prod", pod="checkout-abc123",
)
```
Or via the deployed Agent Engine directly: `scripts/smoke_test.sh [scenario]` (see that script's own header for the scenario list). Streaming variant: `SREAgent.stream_query(...)` — see `agent/main.py`'s `investigate_stream()`.

## 2. Inspect logs / metrics / traces / evidence

- **Logs**: see [Logging](../operations/logging.md) — every investigation emits one structured `sre_agent_run` JSON log line (run ID, cluster, outcome, confidence, tokens, cost) plus per-node `_log_node_tokens` lines.
- **Metrics**: see [Observability](../operations/observability.md) and `iac/agent/monitoring.tf` for the full current alert list (14 policies as of 2026-09-08 — routing failures, GKE/custom-MCP tool failures, Model Armor fail-open, Model Armor guard init failure, Connect Gateway connection failure, latency, token budget, evidence storage failures, and more).
- **Traces**: see [Tracing](../operations/tracing.md) — Cloud Trace, one span per LangGraph node (`langgraph.<node_name>`) plus one per tool call (`mcp.<tool_name>`). Known gap (disclosed, not fixed in this pass): `mcp/server.py` itself has zero OTel instrumentation — the dynamic Connect Gateway connection build has no distinct span; only the agent-side `mcp.<tool_name>` call span exists today.
- **Evidence**: every tool response is written to `gs://<evidence-bucket>/<run_id>/<evidence_id>.json` regardless of outcome (`agent/gcs_client.py::write_evidence`) — this is the full audit trail, including redacted-but-complete raw tool output, independent of whatever the final report shows.

## 3. Diagnose a failure

Start with [Investigation Failure](investigation-failure.md) for the general triage tree. For a SPECIFIC failure shape, jump straight to the matching runbook: [Deployment Failure](deployment-failure.md) · [Gateway Failure](gateway-failure.md) · [Identity Failure](identity-failure.md) · [MCP Failure](mcp-failure.md) · [Routing Failure](routing-failure.md) · [Data/Integration Failure](data-and-integration-failure.md).

Quick first move for almost any "the agent said something wrong" report: pull the `run_id` from the report, then `gsutil ls gs://<evidence-bucket>/<run_id>/` to see exactly what evidence the investigation actually had — most "wrong RCA" reports turn out to be a real evidence gap (stale fixture, missing tool coverage), not a reasoning bug.

## 4. Review / promote / revoke a memory record

**Built 2026-09-08** (Section 8 of the new-assignment expansion work; see `docs/ADR-010-human-approval-before-trusted-memory.md`) — every RCA the agent is confident enough to remember is written `status=pending_review` and is **NOT** recalled into future investigations until explicitly approved:

```bash
# List pending memories for a cluster/namespace
python3 scripts/review_memory.py list --cluster sre-lab --namespace test-incidents --status pending_review

# Approve one -- it becomes recallable
python3 scripts/review_memory.py approve --name <memory resource name from the list output>

# Reject or revoke (excluded from recall either way; reason is recorded)
python3 scripts/review_memory.py reject --name <memory name> --reason "root cause was actually X, not Y"
python3 scripts/review_memory.py revoke --name <memory name> --reason "later investigation contradicted this"
```

Requires `PROJECT_ID`/`MEMORY_BANK_RESOURCE` env vars (`source scripts/init-env.sh` first). See [Memory](../architecture/memory.md) for the full lifecycle design.

## 5. Roll back

Full detail in [Rollback](../operations/rollback.md) — summarized here for the common case (a bad agent-code deploy):

```bash
git checkout <last-known-good-commit>
bash scripts/package_agent.sh
terraform apply -chdir=iac/agent   # re-submits the older source against the same engine resource
bash scripts/attach_gateway_to_engine.sh   # required after ANY engine-touching apply
```

For the custom MCP specifically: redeploy an older image tag (Artifact Registry retains every prior build by commit SHA) via the same `terraform apply` with `-var="custom_mcp_image=<older tag>"`.

---

**Related pages:** [Daily Health Check](../operations/daily-health-check.md) · [Rollback](../operations/rollback.md) · [Memory](../architecture/memory.md) · [Switch LLM/Model](switch-llm-model.md)
