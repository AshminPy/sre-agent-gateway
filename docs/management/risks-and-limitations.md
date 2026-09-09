# Risks and Limitations

> **Implementation Status:** This page IS the honest gap list — every item below is a confirmed finding from direct code/config inspection, not speculation.
> **Last Verified:** 2026-08-20 (items 1-4 and 6-14 re-checked; item 5 resolved; items 15-16 added). **2026-09-07: items 1, 3, 6, 17 corrected — each described a gap that has since closed; see each item for what changed and the evidence.**
> **Owner:** SRE Agent platform team.

Ranked roughly by how much it should matter to a reviewer, most significant first.

## 1. ~~Model Armor is not actually providing content-safety inspection in the live deployment~~ — RESOLVED 2026-09-07

Model Armor is live and wired at two layers: (1) a CONTENT_AUTHZ extension at Agent Gateway (`google_network_services_authz_extension.model_armor`, `iac/agent/agent_gateway.tf`, referencing templates `sre_agent_request`/`sre_agent_response` in `iac/agent/model_armor.tf`, `enforcement_type = "INSPECT_AND_BLOCK"`) inspects and can block request/response traffic through the gateway; (2) floor settings (`google_model_armor_floorsetting`) additionally inspect for malicious URIs at HIGH confidence, `inspect_only = true` (logging only, not yet blocking) — live since 2026-08-25/26. One real, permanent limitation remains and still deserves Security's attention: Google's Streamable HTTP transport never invokes RESPONSE_BODY/MCP-tool-response inspection — an accepted platform limitation, not fixable from our side. Custom MCP responses are separately covered by an application-level guard, `mcp/response_guard.py` (see item 17 below). **`docs/governance/security.md`'s own Model Armor section may still describe the earlier, now-superseded finding — check and correct it separately; it is not edited here.**

## 2. The confidence-scoring policy is explicitly self-labeled uncalibrated

`POLICY_VERSION = "1.0.0-uncalibrated"`. The scoring *mechanism* is real and evidence-grounded (see [Confidence Scoring](../architecture/confidence.md)), but the specific numeric thresholds haven't been validated against real incident outcomes. Any commitment made based on "the agent is X% accurate at Y confidence band" should wait for that calibration work.

## 3. ~~The fallback Kubernetes path (custom MCP) doesn't work today~~ — RESOLVED 2026-09-04

The custom Cloud Run MCP (`sre-k8s-mcp`) is live in production: the GitHub repo variable `ENABLE_CUSTOM_MCP=true` has driven every CI apply since 2026-08-07, ingress was changed to `INGRESS_TRAFFIC_ALL` (no Load Balancer/NEG needed — that was never the real call path), and the connectivity env vars/Dockerfile changes are wired. Live E2E proof (2026-09-04, run `run_20260904_215412_kiny`): a real investigation ran Agent → Agent Gateway → custom MCP Cloud Run → Connect Gateway → the `sre-lab` `kind` cluster, correctly identified the fixture's broken image, confidence 1.0, zero fabrication — and dozens of successful real investigations have used this path since (2026-09-04 through 2026-09-07). **A GKE Remote MCP outage today has a working fallback.** See [MCP Architecture](../architecture/mcp-architecture.md). One known limitation is unrelated to this fix and still real: the custom MCP is single-cluster-per-deployment.

## 4. On-prem/non-GKE cluster support is not production-ready

Proven only via manual `kubectl` testing and a locally-run integration test — the deployed agent cannot reach an on-prem cluster today. See [GKE vs Non-GKE Access](../architecture/gke-vs-nongke.md).

## 5. ~~Several metrics likely double-count~~ — FIXED 2026-08-13

Issue #75 is closed. Two completion-event emitters existed per investigation (`agent/main.py`'s `sre_agent_run` stdout event and `rca_builder.py`'s richer `sre-agent-investigations` Cloud Logging entry), and 8 metrics matched both. Fixed by scoping those metrics to `event_type="sre_agent_run"` — **not** by `logName`, so a `logName` grep alone will wrongly suggest this is still open. Verified in `iac/agent/monitoring.tf`. See [Observability](../operations/observability.md).

## 6. ~~IAP authorization fails open~~ — RESOLVED 2026-09-05, now fails closed

`iac/agent/variables.tf`'s `authz_fail_open` default flipped `true`→`false` (PR #249, merged 2026-09-05) — confirmed live: `fail_open = false`. An IAP/REQUEST_AUTHZ outage now blocks egress rather than silently allowing it; this removes the earlier rollout-safety tradeoff and its missing risk-acceptance record described here. **`docs/governance/security.md` may still describe the earlier fail-open behavior — check and correct it separately; it is not edited here.**

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

## 16. Agent's own observability write to Cloud Logging returns 403 (issue #139, RESOLVED for rca_builder.py; 3 sibling call sites deployed, pending live validation)

**Root cause found, live-verified 2026-08-23:** the default gRPC transport never resolved a registry match through Agent Gateway for `logging.googleapis.com`/`logging.mtls.googleapis.com` (confirmed via 180 days of gateway logs — every gRPC call to this host showed an empty `agentGatewayInfo`, unlike every other working destination). **Fix:** switched the Cloud Logging client to `_use_grpc=False` (HTTP_JSON transport) and re-registered the `us-central1-logging` Agent Registry entry to `protocolBinding=HTTP_JSON`. `rca_builder.py`'s write (`sre-agent-investigations` log): 3/3 clean live runs after the fix, zero 403s. The identical fix was applied to the 3 sibling call sites sharing this pattern — `tool_executor.py`, `mcp_router.py`, `gcs_client.py` — deployed (PR #175) but **not yet independently live-validated**, since each only writes on a genuine tool/routing/evidence-storage failure that hasn't occurred naturally yet (tracker row 159). **Investigation results were never affected** even before the fix — the separate, always-working stdout observability path in `agent/main.py` covers the same data independently.

---

## 17. ~~Custom/fallback MCP traffic is not Model Armor-inspected~~ — RESOLVED, by a different compensating control than originally proposed (2026-08-25 finding, added 2026-08-30, corrected 2026-09-07)

The floor-setting gap described here is still real — Model Armor's floor-setting API still only
accepts `AI_PLATFORM`/`GOOGLE_MCP_SERVER` as integrated services, so it never sees custom-MCP
traffic directly. But rather than the app-level `_sanitize()` fix originally proposed, custom
MCP *responses* are now covered by a dedicated, live-deployed guard: `mcp/response_guard.py` —
it blocks on a real `MATCH_FOUND` and fails open only on a genuine Model Armor API error
(monitored via the `mcp_model_armor_fail_open` alert, which has never fired). Full original
finding: `archive/SUPERSEDED_2026-08-25_custom-mcp-model-armor-coverage.md`.

---

None of the above are described elsewhere in this knowledge base as fully solved — every relevant page cross-references back to this list. Treat this page as the canonical "what's actually still open" summary.

**Related pages:** [Current State](CURRENT-STATE.md) · [Executive FAQ](executive-faq.md) · [Security Operations](../governance/security.md) · [Documentation Validation Report](../../archive/SUPERSEDED_2026-08-20_DOCUMENTATION-VALIDATION-REPORT.md)
