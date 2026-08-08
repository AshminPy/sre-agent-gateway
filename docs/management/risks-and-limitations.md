# Risks and Limitations

> **Implementation Status:** This page IS the honest gap list — every item below is a confirmed finding from direct code/config inspection, not speculation.
> **Last Verified:** 2026-08-08
> **Owner:** SRE Agent platform team.

Ranked roughly by how much it should matter to a reviewer, most significant first.

## 1. Model Armor is not actually providing content-safety inspection in the live deployment

The Terraform templates exist and look correctly configured; neither the app-layer nor gateway-layer inspection path is actually invoked in the current live config. Confirmed both via static config analysis and a same-day live test. **See [Security Operations](../governance/security.md#️-model-armor--the-most-significant-governance-finding-in-this-review) for the full evidence.** This is the single most important finding in this entire knowledge base for a Security stakeholder.

## 2. The confidence-scoring policy is explicitly self-labeled uncalibrated

`POLICY_VERSION = "1.0.0-uncalibrated"`. The scoring *mechanism* is real and evidence-grounded (see [Confidence Scoring](../architecture/confidence.md)), but the specific numeric thresholds haven't been validated against real incident outcomes. Any commitment made based on "the agent is X% accurate at Y confidence band" should wait for that calibration work.

## 3. The fallback Kubernetes path (custom MCP) doesn't work today

`enable_custom_mcp=false` live, and even if enabled, no network path exists to reach the service (`INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` with no Load Balancer/NEG built). **A GKE Remote MCP outage today has no working fallback**, despite the code containing auto-fallback logic. See [MCP Architecture](../architecture/mcp-architecture.md).

## 4. On-prem/non-GKE cluster support is not production-ready

Proven only via manual `kubectl` testing and a locally-run integration test — the deployed agent cannot reach an on-prem cluster today. See [GKE vs Non-GKE Access](../architecture/gke-vs-nongke.md).

## 5. Several metrics likely double-count

`errors`, `confidence_band`, `escalations`, `invocations`, `investigation_cost_usd`, `investigation_latency_seconds` are not scoped by `logName` and likely reflect roughly 2x the real event count, due to a duplicate emission path. See [Observability](../operations/observability.md). Low severity individually, but worth fixing before this system's metrics are trusted for external reporting.

## 6. IAP authorization fails open

An IAP outage silently degrades to "unauthorized egress allowed" rather than blocking the agent. A deliberate rollout-safety tradeoff, but one that doesn't appear to have a formal risk-acceptance record. See [Security Operations](../governance/security.md).

## 7. `clusters.json` is wiped on every `terraform apply`

The multi-cluster registry file is generated from a single-cluster-only Terraform template — any manually-added second cluster is destroyed on the next apply. This directly blocks scaling to more than one cluster without a fix. See [Cluster Routing](../architecture/cluster-routing.md).

## 8. No automated eval-quality gate in CI

A model/prompt change can be merged and deployed without the golden evaluation suite running automatically — only a single-scenario smoke test gates deployment. A silent quality regression is possible today. See [Evaluation](../architecture/evaluation.md).

## 9. No human-approval step before a memory writes to long-term storage

Gated by an automated confidence check only. The fields needed for human review exist; the workflow doesn't. See [Memory](../architecture/memory.md).

## 10. Connect Gateway doesn't audit-log successful reads

Only denied/blocked writes are captured by default — a full read-only investigation via that path (when it's eventually production-ready) would leave no audit trail of what was actually read. See [Security Operations](../governance/security.md#audit-logs).

## 11. No PagerDuty integration exists

Every reference to PagerDuty in this system today is a schema placeholder (`pagerduty_incident_id` always `None`). Live triggering is currently manual/CLI-based (`invoke_agent.py`). See [Reliability](../governance/reliability.md).

## 12. No documented `max_instances` ceiling

Agent Engine's compute scaling ceiling is not visible from this repo's Terraform — unverified platform default applies. See [Agent Engine](../architecture/agent-engine.md).

## 13. No tested disaster-recovery drill

"Everything is rebuildable from Terraform" is true in principle based on code inspection, unverified in practice — no evidence of an actual full-rebuild drill having been run. See [Disaster Recovery](../operations/disaster-recovery.md).

## 14. No formal SLOs are committed to

Alert thresholds exist as operational tripwires; no published service-level commitment exists yet. See [Reliability](../governance/reliability.md).

---

None of the above are described elsewhere in this knowledge base as fully solved — every relevant page cross-references back to this list. Treat this page as the canonical "what's actually still open" summary.

**Related pages:** [Executive FAQ](executive-faq.md) · [Security Operations](../governance/security.md) · [Documentation Validation Report](../README.md#documentation-validation-report)
