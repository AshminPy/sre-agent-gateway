# Risks and Limitations

> **Implementation Status:** This page IS the honest gap list — every item below is a confirmed finding from direct code/config inspection, not speculation.
> **Last Verified:** 2026-08-20 (items 1-4 and 6-14 re-checked; item 5 resolved; items 15-16 added)
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

## 5. ~~Several metrics likely double-count~~ — FIXED 2026-08-13

Issue #75 is closed. Two completion-event emitters existed per investigation (`agent/main.py`'s `sre_agent_run` stdout event and `rca_builder.py`'s richer `sre-agent-investigations` Cloud Logging entry), and 8 metrics matched both. Fixed by scoping those metrics to `event_type="sre_agent_run"` — **not** by `logName`, so a `logName` grep alone will wrongly suggest this is still open. Verified in `iac/agent/monitoring.tf`. See [Observability](../operations/observability.md).

## 6. IAP authorization fails open

An IAP outage silently degrades to "unauthorized egress allowed" rather than blocking the agent. A deliberate rollout-safety tradeoff, but one that doesn't appear to have a formal risk-acceptance record. See [Security Operations](../governance/security.md).

## 7. ~~`clusters.json` is wiped on every `terraform apply`~~ — FIXED 2026-08-09

Multi-cluster support now exists (`var.additional_clusters`, `iac/agent/variables.tf`), with a real, enforcing collision guard and a live-verified backward-compatible default. See [Cluster Routing](../architecture/cluster-routing.md). One related gap remains, not fixed by this change: adding a cluster in a **different** GCP project than the existing one currently gets no IAM grant (`iac/gke-access` is hardwired to one project) and will `403` at runtime — see [Adding a New GKE Cluster](../runbooks/add-gke-cluster.md).

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

## 15. Agent Platform Console Traces tab does not update (issue #164, OPEN)

**Fixed:** our application's own OpenTelemetry provider race. The #130 fix (PR #136) made our tracer initialise early enough to reliably win OpenTelemetry's one-time global-provider slot, which blocked Agent Engine's managed provider from installing. PR #165 removed that competition — `get_tracer()` now builds its own local `TracerProvider` instead — restoring the managed tracer path.

**Still unresolved:** the Console symptom itself. The Agent Platform Console's Traces tab has not updated since 2026-08-13, and the Deployments list still shows "Learn more" rather than "Enabled" for Telemetry collection. **The Console-side root cause is not confirmed.** Three config-level hypotheses were tested live and ruled out (2026-08-14 to 2026-08-16), the last being `OTEL_SEMCONV_STABILITY_OPT_IN` / `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`; each was reverted afterwards.

**Scope of the impact:** Cloud Trace itself is working — live run `run_20260816_083409_lpak` produced 54 correctly-parented spans. What is unreliable is the *Console view*, not trace collection. Use Cloud Trace directly rather than the Console tab until #164 closes.

## 16. Agent's own observability write to Cloud Logging returns 403 (issue #139, OPEN)

`rca_builder.py._write_observability_log()`'s gRPC write to `logging.googleapis.com` still returns 403 despite `logging`/`logging-mtls` being correctly registered with `protocolBinding=GRPC`. Root cause unconfirmed. **Investigation results are unaffected** — a separate, always-working stdout observability path in `agent/main.py` already covers this — so the practical impact is a missing structured audit record, not degraded RCA quality.

---

None of the above are described elsewhere in this knowledge base as fully solved — every relevant page cross-references back to this list. Treat this page as the canonical "what's actually still open" summary.

**Related pages:** [Executive FAQ](executive-faq.md) · [Security Operations](../governance/security.md) · [Documentation Validation Report](../README.md#documentation-validation-report)
