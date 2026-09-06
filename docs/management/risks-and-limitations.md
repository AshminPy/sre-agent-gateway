# Risks and Limitations

> **Implementation Status:** This page IS the honest gap list — every item below is a confirmed finding from direct code/config inspection, not speculation.
> **Last Verified:** 2026-09-06 (items 1, 3, 4, 6, 17 corrected — each was resolved or superseded since 2026-08-20; items 18-21 added)
> **Owner:** SRE Agent platform team.

Ranked roughly by how much it should matter to a reviewer, most significant first.

## 1. Model Armor's response-side inspection never fires — a confirmed Google platform limitation (corrected 2026-09-06)

**Superseded finding, corrected:** the previous claim here ("Model Armor is not actually providing content-safety inspection in the live deployment") was accurate on 2026-08-20 but is now wrong — the original wiring attempt used the wrong service hostname. Gateway CONTENT_AUTHZ is real and live: it genuinely inspects and BLOCKS MCP `tools/call` REQUEST bodies (`enforcement_type = "INSPECT_AND_BLOCK"`, live-verified with real `REQUEST_BODY: CONTENT_MODIFIED` gateway logs).

**What is still a real, current gap**: RESPONSE-side inspection never fires, for either MCP source (custom Cloud Run MCP or GKE Remote MCP). Root cause, confirmed against Google's own current documentation: "Streamable HTTP/SSE for MCP" is explicitly excluded from gateway sanitization, and Streamable HTTP is the MCP spec's own current transport with no viable non-streaming remote alternative. A `json_response=True` fix was tried and reverted after live testing showed it made no measurable difference — this is a transport-level exclusion, not a fixable wire-format detail. A malicious payload placed in Kubernetes resource data (proven with Google's own guaranteed-detection Safe Browsing test URL) reaches the agent's evidence completely unblocked via CONTENT_AUTHZ alone.

**Compensating control, not yet merged**: `mcp/response_guard.py` (branch `poc/mcp-response-guard-model-armor`) calls Model Armor directly from the custom MCP server itself, closing this gap for that source specifically. Live-proven to genuinely block a real malicious response. Not yet reviewed/merged; its fail-open-on-Model-Armor-error behavior is an explicit open decision (see item 19 below).

Full evidence and the three-mechanism breakdown (CONTENT_AUTHZ vs. floor settings vs. this new guard): [Security Operations](../governance/security.md#model-armor--three-distinct-mechanisms-each-with-different-enforcement-corrected-2026-09-06).

## 2. The confidence-scoring policy is explicitly self-labeled uncalibrated

`POLICY_VERSION = "1.0.0-uncalibrated"`. The scoring *mechanism* is real and evidence-grounded (see [Confidence Scoring](../architecture/confidence.md)), but the specific numeric thresholds haven't been validated against real incident outcomes. Any commitment made based on "the agent is X% accurate at Y confidence band" should wait for that calibration work.

## 3. ~~The fallback Kubernetes path (custom MCP) doesn't work today~~ — FIXED 2026-09-04

`enable_custom_mcp` defaults `false` in Terraform code, but the GitHub repo variable `ENABLE_CUSTOM_MCP=true` has driven every real CI apply since 2026-08-07 — the service is live. The network-path blocker (`INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` with no LB/NEG built) was fixed by changing ingress to `INGRESS_TRAFFIC_ALL`, with IAM (`roles/run.invoker`, scoped to the agent identity only) as the real control — live-verified: unauthenticated → 403/404, unauthorized-but-real-identity → 401. GKE Remote MCP now has a genuinely working fallback. See [MCP Architecture](../architecture/mcp-architecture.md).

## 4. ~~On-prem/non-GKE cluster support is not production-ready~~ — FIXED 2026-09-04

Real investigations against the on-prem `sre-lab` cluster now succeed end-to-end through the actual deployed Agent Engine → Agent Gateway → custom MCP → Connect Gateway path (not just manual `kubectl`/local-`pytest` testing). Still open: Connect Gateway fleet registration/RBAC remains manual, not Terraform-managed; single-cluster-per-deployment (`@lru_cache(maxsize=1)`); `DATA_READ` audit logging for Connect Gateway is still off. See [GKE vs Non-GKE Access](../architecture/gke-vs-nongke.md) for the full current picture.

## 5. ~~Several metrics likely double-count~~ — FIXED 2026-08-13

Issue #75 is closed. Two completion-event emitters existed per investigation (`agent/main.py`'s `sre_agent_run` stdout event and `rca_builder.py`'s richer `sre-agent-investigations` Cloud Logging entry), and 8 metrics matched both. Fixed by scoping those metrics to `event_type="sre_agent_run"` — **not** by `logName`, so a `logName` grep alone will wrongly suggest this is still open. Verified in `iac/agent/monitoring.tf`. See [Observability](../operations/observability.md).

## 6. IAP authorization fails open — validated fix exists, not yet merged

Still `authz_fail_open = true` on `main` today. An IAP/REQUEST_AUTHZ extension outage silently degrades to "unauthorized egress allowed" rather than blocking the agent. A fail-closed fix is validated on an unmerged branch (`fix/content-authz-json-response-and-fail-closed`) — a real revoke/retry test showed correct denial (403, no bypass, fast-fail, no fabricated RCA). The extension-*unreachable* case specifically (as opposed to a revoked authorization) was not independently live-tested — IAP has no safe way to simulate its own outage. See [Security Operations](../governance/security.md#iap-fail-open--fix-validated-not-yet-merged).

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

## 17. Custom/fallback MCP response traffic is not Model Armor-inspected via the gateway (superseded 2026-09-06, see item 1)

**Superseded, more precise finding available.** The original 2026-08-25 conclusion (floor settings don't support a custom-MCP integration type) is still true as far as it goes, but the *complete* picture is item 1 above: this is specifically a RESPONSE-side gap in CONTENT_AUTHZ (a documented Google Streamable HTTP/SSE transport exclusion), not solely a floor-setting integration-type limitation, and it also applies to GKE Remote MCP's responses, not just the custom MCP's. The scoped fix once proposed here (wire `_sanitize()` into `tool_executor.py`) was never built; a different, better-scoped, and live-tested fix now exists instead — see item 1 and item 19. Original evidence: `archive/SUPERSEDED_2026-08-25_custom-mcp-model-armor-coverage.md`.

## 18. GKE Remote MCP has the identical response-side CONTENT_AUTHZ gap as the custom MCP (2026-09-06)

Both MCP sources share the same Agent Gateway and the same CONTENT_AUTHZ extension. A real gateway log for a GKE Remote MCP `tools/call` (hostname `container.googleapis.com`) shows the identical `serviceExtensionInfo` signature as the custom MCP: `REQUEST_BODY: CONTENT_MODIFIED`, `RESPONSE_BODY` never appears. Google does not publish GKE Remote MCP's internal transport, so the exact mechanism is formally unproven — but the observed, tested behavior through our own gateway is identical to the confirmed-excluded transport, so the practical effect (no response-side blocking via CONTENT_AUTHZ) is the same. No compensating guard exists for this path today (the new response guard, item 1/19, only covers the custom MCP). See [Security Operations](../governance/security.md#model-armor--three-distinct-mechanisms-each-with-different-enforcement-corrected-2026-09-06).

## 19. Response-guard Model Armor failure policy is an open decision, not yet approved (2026-09-06)

`mcp/response_guard.py` (unmerged POC) fails **open** if the Model Armor API itself errors or is unreachable — it mirrors an existing pattern elsewhere in this codebase (`agent/main.py::_sanitize()`), but that precedent is explicitly not automatic approval for a production/work-repo port. Before merging: decide whether MCP response enforcement should (A) fail open with alerting, or (B) fail closed / return a controlled tool error when Model Armor cannot inspect. Not implemented either way as of this writing.

## 20. Run_id correlation across Agent Gateway and Model Armor logs is limited

Neither the Agent Gateway's own request logs (`networkservices.googleapis.com/Gateway`) nor Model Armor's `sanitize_operations` logs carry the agent's `run_id`. Correlating a specific investigation to a specific gateway/Model Armor log entry requires matching on timestamp window + hostname + tool name — reliable when traffic is low (as in all testing to date) but not a guaranteed, unambiguous join at higher concurrency. Anyone building an automated audit/alerting pipeline on top of these logs should account for this rather than assume a clean join key exists.

## 21. Issue #246 — cluster-scoped Kubernetes tools receive an invalid namespace argument (OPEN)

Confirmed open via `gh issue view 246` (2026-09-06). A real, unresolved functional bug, independent of the Model Armor/CONTENT_AUTHZ work above — not an accepted platform limitation, an actual defect to fix.

---

None of the above are described elsewhere in this knowledge base as fully solved — every relevant page cross-references back to this list. Treat this page as the canonical "what's actually still open" summary.

**Related pages:** [Current State](CURRENT-STATE.md) · [Executive FAQ](executive-faq.md) · [Security Operations](../governance/security.md) · [Documentation Validation Report](../../archive/SUPERSEDED_2026-08-20_DOCUMENTATION-VALIDATION-REPORT.md)
