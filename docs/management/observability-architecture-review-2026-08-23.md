# SRE Agent Gateway — Observability & Monitoring Architecture Review
**Phase 0 — Validation only. No code, config, or infra was changed to produce this report.**
Date: 2026-08-23. Repo: `/Users/ashmin/projects/sre-agent-gateway`.

> **Resolved since this review (added 2026-09-07) — this document is a dated Phase 0 snapshot,
> not current state. See [Current State](CURRENT-STATE.md) for what's true today.**
> - **§3 (telemetry fail-open violation) — FIXED.** `agent/main.py:872-899` now separates
>   "the graph completed successfully" from "the graph crashed," so a failure during
>   finalization/logging no longer reports a genuinely successful investigation as failed.
> - **§7 (GCS evidence-store span) — FIXED.** `agent/gcs_client.py:39-45` now wraps the write
>   in `tracer.start_as_current_span("gcs.write_evidence")`.
> - **§1's `register_endpoints.py` GRPC-only-hostname landmine — MOOT.** The script was
>   retired 2026-09-04 (`archive/RETIRED_2026-09-04_register_endpoints.py`); Terraform now
>   manages Agent Registry endpoint registrations directly (`iac/agent/agent_registry.tf`,
>   `agent_registry_mcp.tf`, commit `a3f2ba1`).
> - **§6 (MCP call-level span in `agent/mcp_client.py`) — STILL OPEN.** Confirmed: no
>   `start_as_current_span` call exists anywhere in `agent/mcp_client.py`. Not done.
> - **§2 (the 5 broken alerts)** and everything else below is not re-verified by this
>   addendum — treat the rest of this document as historical unless separately confirmed.

Every claim below is tagged VERIFIED (checked against live GCP, the actual repo, or a direct official-doc fetch) or UNVERIFIED/NOT FOUND (searched for, not confirmed) — never presented as fact without one of those tags, per your instructions.

---

## Executive summary — 4 things that matter most

1. **Real bug found, unrelated to OTLP: telemetry can fail a successful investigation.** `agent/main.py`'s `_finalize_investigation_result()` (the function that writes the observability log + flushes traces) runs *inside* the same `try/except` as the actual investigation graph. If it raises — e.g. `json.dumps()` on a bad value — a successful investigation gets reported as `status: failed`. This directly violates your own "telemetry must never fail the investigation" requirement. **IMPLEMENT NOW.**
2. **The 5 broken alerts** (already scoped in this conversation) — `resource.type="global"` should be `"cloud_run_revision"`, confirmed live. Still waiting on your go-ahead to open that PR.
3. **A landmine in `scripts/register_endpoints.py`** would silently re-break issue #139 on a full project rebuild/DR — it still hardcodes Cloud Logging as GRPC-only. Cheap fix, **IMPLEMENT NOW**.
4. **OTLP wholesale migration: DO NOT IMPLEMENT NOW.** GA, real, but no demonstrated advantage over your current (just-fixed, working) path, and Agent Gateway compatibility with the Telemetry API is unverified by Google's own docs — moving a freshly-fixed path to an unproven one is added risk with no proven benefit, which is exactly what you told me not to do.

---

## 1. Cloud Logging write path — preserve as baseline (per your instruction)

**Current implementation (VERIFIED, repo):** 4 call sites — `agent/gcs_client.py:77`, `agent/nodes/tool_executor.py:85`, `agent/nodes/mcp_router.py:84`, `agent/nodes/rca_builder.py:149` — all `cloud_logging.Client(project=..., _use_grpc=False)`. HTTP_JSON registry binding for `us-central1-logging`. Live-confirmed clean for `rca_builder.py`'s path (3/3 runs, 2026-08-23); the other 3 sites deployed but not yet naturally exercised (tracker row 159, already agreed to wait for a real failure).

**Gap (VERIFIED, repo):**
- `scripts/register_endpoints.py:57-66` — `_GRPC_ONLY_HOSTNAMES` still lists `logging.googleapis.com`/`logging.mtls.googleapis.com`. The script only skips re-registering entries that already exist (`register_endpoints.py:377-386`) — safe today, but a full re-registration (fresh project, DR, manual delete+recreate) would put GRPC back and reintroduce #139.
- `tests/test_observability.py:45-54` mocks `cloud_logging.Client` with `lambda *a, **k` — swallows `_use_grpc` entirely. A future refactor could drop the flag from any of the 4 call sites and no test would catch it.
- 2 stale docs: `docs/management/risks-and-limitations.md:73-75` and `docs/management/implemented-vs-planned-matrix.md:139` still describe #139 as OPEN/unconfirmed.
- No Agent Registry protocol binding exists as Terraform (`iac/` has no `google_agent_registry_*` resource) — registration is entirely imperative via the script above. This means `terraform apply` can't revert the fix (it never touches it), but it also means nothing declaratively guards it either.

**Google capability considered:** OTLP log ingestion (GA) — explicitly NOT evaluated as a fix for this, per your instruction. Evaluated only as future architecture in §9.

**Benefit of closing the gap:** prevents the exact same bug recurring on the next full environment rebuild, with zero runtime risk.

**Risk:** SAFE — code/test/doc only, no behavior change to a working path.

**Required changes:** `scripts/register_endpoints.py:57-66` (remove logging hosts from the GRPC-only set, or make the set reflect only genuinely GRPC-only hosts if any remain), `tests/test_observability.py` mock (capture kwargs, assert `_use_grpc=False`), the 2 doc files.

**Testing:** new unit test asserting `protocol_binding_for("logging.googleapis.com") == "HTTP_JSON"`; extend the existing mock to assert `_use_grpc` on all 4 call sites; re-run `make smoke`.

**Rollback:** plain `git revert`, no infra impact.

**Recommendation: IMPLEMENT NOW.**

---

## 2. The 5 broken alert policies

Already scoped earlier in this session — `iac/agent/monitoring.tf`: `routing_failures`, `gke_mcp_failures`, `custom_mcp_failures`, `repeated_tool_failures`, `evidence_storage_failures` filter on `resource.type="global"`; live-verified (project `sreagent-t2-demo`) the real value is `cloud_run_revision`.

**Recommendation: IMPLEMENT NOW — pending your go-ahead (not yet given).**

---

## 3. Telemetry fail-open violation (new finding)

**Current implementation (VERIFIED, repo):** `agent/main.py:_finalize_investigation_result()` (lines 438-599) is called at `main.py:683` and `main.py:792`, both *inside* the outer `try:` that also runs the real investigation (`main.py:618`/`712`), whose `except Exception as exc:` (`685-687`/`794-796`) returns `{"error": str(exc), "status": "failed"}`. Inside that function: `print(json.dumps(obs_event, ...))` (`main.py:568`) and a `flush_traces(...)` call (`581`) — neither individually guarded.

**Gap:** if `obs_event` ever contains a non-JSON-serializable value, or anything else in this ~160-line function raises, a **genuinely successful investigation is reported to the caller as failed** — purely from an observability-formatting bug. This is the exact failure mode your "Reliability requirement" section describes as unacceptable.

**Benefit of fixing:** closes a real correctness gap, directly matches your stated requirement (telemetry = DEGRADED, investigation = SUCCESS, not the reverse).

**Risk:** SAFE — adding a try/except only isolates failure, doesn't touch happy-path behavior. Same defensive pattern already used in `gcs_client.py`, `tool_executor.py`, `mcp_router.py`, `rca_builder.py` — this function is the one place that pattern was missed.

**Required changes:** `agent/main.py` — wrap `_finalize_investigation_result()`'s body (or both call sites) in try/except that logs a warning and returns/continues rather than propagating.

**Testing:** unit test that forces `obs_event` to contain a non-serializable value and asserts `investigate()`/`investigate_stream()` still return `status: success` for an otherwise-successful run.

**Rollback:** trivial, pure code change.

**Recommendation: IMPLEMENT NOW.**

---

## 4. Structured logging / correlation IDs

**Current (VERIFIED, repo):** `run_id` appears in nearly every node's log lines, but as plain printf-style args, not structured/JSON fields. Only 2 paths are fully structured JSON with `trace_id`: `rca_builder.py`'s observability log and `main.py`'s `obs_event`. **No `span_id` field exists anywhere in the codebase** — `get_trace_id_hex()` (`otel.py:108-123`) only returns trace_id.

**Gap:** most log lines aren't machine-correlatable beyond a `run_id` substring match; even where `trace_id` exists, there's no `span_id` to pinpoint the exact span.

**Google capability:** Cloud Logging's `logging.googleapis.com/trace` and `/spanId` structured fields auto-link log entries to Cloud Trace in the console — UNVERIFIED here whether current log calls populate these exact field names (not checked this session; would need a targeted follow-up against Cloud Logging structured-log docs before implementing).

**Benefit:** one-click log→trace/span navigation in Cloud Console; correlates a specific tool-failure log to the exact span instance, not just the run.

**Risk:** LOW-MEDIUM — touches many call sites; needs care not to spike log volume or leak fields already flagged as sensitive elsewhere in the codebase.

**Required changes:** broad — likely a shared logging adapter injecting run_id/trace_id/span_id, probably added to `agent/otel.py` or a new helper, then adopted incrementally.

**Testing:** confirm emitted entries carry the correlation fields via a real Cloud Logging query, and confirm Console actually shows the trace link.

**Rollback:** revert the adapter.

**Recommendation: IMPLEMENT LATER** — real value, broad surface area, not urgent since `run_id` already gives coarse correlation today.

---

## 5. Native OTel Metrics API (counters/histograms)

**Current (VERIFIED, repo):** all "metrics" today are accumulated dict fields (token accounting in `agent/llm/accounting.py:19-42`, latency fields in `mcp_client.py`/`rca_builder.py`/`main.py`) turned into either Terraform log-based metrics or span attributes. **No `opentelemetry.metrics` Counter/Histogram instrument exists anywhere in the repo.** No direct Cloud Monitoring client usage either — Monitoring only exists as Terraform resources layered on top of Cloud Logging entries.

**Gap:** every metric depends on the chain *write to Cloud Logging → log-based metric → alert* — the exact chain that just broke in #139. A native metrics pipeline wouldn't have that dependency for pure counters.

**Google capability (VERIFIED, official docs):** OTLP metrics ingestion via Telemetry API is GA (`docs.cloud.google.com/monitoring/docs/release-notes`, Aug 5 2026 entry, confirmed by direct fetch). A Collector is not required — SDK can export directly to `telemetry.googleapis.com`.

**Benefit:** removes the Cloud-Logging-as-metrics-transport dependency for counters that don't also need to be human-readable log lines (e.g. `tool_failures`, `routing_failures`).

**Risk: MEDIUM.** New dependency (OTel Metrics SDK + exporter), new IAM role (`roles/telemetry.writer`), and — critically — **whether Telemetry API calls route correctly through Agent Gateway is UNVERIFIED.** Google's docs don't address egress-allowlist architectures like Agent Gateway's registry model at all (confirmed NOT FOUND during research), and #139 itself proves this exact category of gateway/registry interaction is where things break specifically in this environment.

**Required changes:** new `agent/otel_metrics.py`-style module; new Terraform alert policies pointed at OTLP-native metrics; a **live validation proving Telemetry API calls actually pass through Agent Gateway** before trusting it for anything production-facing.

**Testing:** the same category of live-validation #139 needed — do not assume "just works" through the gateway.

**Rollback:** additive — old log-based metrics stay live until the new path is proven, retire second.

**Recommendation: IMPLEMENT LATER, as a scoped experiment only** (e.g. migrate just `tool_failures`) — not a wholesale migration. Real advantage on paper, but must be proven against Agent Gateway specifically first, per your own "no migration without demonstrated advantage" rule.

---

## 6. MCP call-level tracing

**Current (VERIFIED, repo):** no dedicated span per MCP call — `agent/mcp_client.py:call_tool` (398-586) measures `duration_s` as a plain returned field, never opens a span itself. Only the enclosing `@trace_node("langgraph.tool_executor")` span covers it, and that span's attributes don't include per-call `duration_s`. Both `gke_remote_mcp` and `k8s_mcp` sources share this (lack of) treatment identically.

**Gap:** no MCP-call-level view in the Cloud Trace waterfall — only in logs/state.

**Google capability (VERIFIED with caveats, official docs):** Remote MCP servers can auto-generate `tools/call` spans (real, confirmed via `docs.cloud.google.com/mcp/monitor-mcp-tool-use-with-cloud-trace`) — but **which specific servers support it is NOT published as a checkable list** (the `mcp/supported-products` page has no Cloud Trace column; confirmed by direct fetch). Your `k8s_mcp` source is a **self-hosted custom MCP, not a Google-managed Remote MCP server** — Google's auto-tracing almost certainly doesn't apply to it at all. Applicability to `gke_remote_mcp` is unconfirmed. Google's docs also don't address duplicate-span risk if you instrument your own calls too (confirmed NOT FOUND) — an open gap in their documentation, not a "no."

**Benefit of self-instrumenting instead:** full control, works identically for both MCP sources, no duplicate-span risk (since you'd own the only span).

**Risk:** LOW — purely additive.

**Required changes:** `agent/mcp_client.py:call_tool` — wrap the existing timing logic in `get_tracer().start_as_current_span(f"mcp.{tool_name}")` with `tool`, `mcp_source`, `duration_ms`, `status` attributes.

**Testing:** confirm spans appear nested correctly under the tool_executor node span in Cloud Trace.

**Rollback:** trivial.

**Recommendation: IMPLEMENT NOW** — cheap, and Google's auto-tracing coverage for your specific sources (one custom, one unconfirmed) isn't reliable enough to depend on instead.

---

## 7. Evidence-store (GCS) tracing

**Current (VERIFIED, repo):** zero tracing on `agent/gcs_client.py:write_evidence`/`redact` — only plain log lines plus the failure-path Cloud Logging event. No span/duration metric for GCS write latency; only indirect coverage via the enclosing `evidence_extractor` node span, which carries no GCS-specific attributes.

**Gap:** evidence storage is a blind spot in exactly the correlation chain your primary objective names (`... → evidence storage → RCA/confidence result`).

**Benefit:** closes a gap you explicitly called out as required.

**Risk:** LOW — additive span around existing code.

**Required changes:** `agent/gcs_client.py:write_evidence` — wrap in a span with `run_id`, `evidence_id`, `bucket`, `duration_ms`, `ok` attributes (per your candidate schema).

**Testing:** confirm span appears nested under `evidence_extractor`'s node span.

**Rollback:** trivial.

**Recommendation: IMPLEMENT NOW.**

---

## 8. Span attribute schema — current vs. your candidate list

**Current (VERIFIED, `agent/otel.py:162-227`)** — namespaced `sre.*`: `sre.node`, `sre.run_id`, `sre.incident_id`, `sre.step`, `sre.status`, `sre.confidence`, `sre.confidence_band`, `sre.cluster`, `sre.region`, `sre.namespace`, `sre.pod`, `sre.incident_type`, `sre.evidence_count`, `sre.tool_calls`, `sre.tokens_total`, `sre.estimated_cost_usd`, plus a `sre.result.*` family (status/confidence/confidence_band/tokens_total/cost/loop_exit_reason/action_tool/action_source/latest_tool/latest_source/latest_ok/new_evidence_ids/new_evidence_count/error_count) and `sre.node.success`/`sre.node.error_type`. Separately, `gemini_adapter.py:142-149` emits OTel semantic-convention `gen_ai.*` attributes (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`/`output_tokens`) on their own child span.

**Diff against your candidate schema:**
| Candidate field | Status |
|---|---|
| `run_id`, `incident_id` | ✅ present (`sre.run_id`, `sre.incident_id`) |
| `langgraph.node` | ✅ present, different name (`sre.node`) |
| `llm.provider`, `llm.model` | ✅ present, different name/convention (`gen_ai.system`, `gen_ai.request.model`) |
| `tokens_input`, `tokens_output`, `tokens_total` | partial — `tokens_total` on the node span (`sre.tokens_total`); input/output only on the `gen_ai.*` LLM child span, not the node span |
| `investigation_iteration` | present as `sre.step`, different name |
| `root_cause_confidence` | present as `sre.confidence`/`sre.result.confidence`, different name |
| `loop_exit_reason` | ✅ present (`sre.result.loop_exit_reason`) |
| `evidence_count` | ✅ present (`sre.evidence_count`) |
| `error.type` | partial — `sre.node.error_type` exists but is per-node-exception scoped, not a general field |
| `agent.name`, `agent.version` | ❌ NOT FOUND anywhere |
| `mcp.server`, `mcp.tool` | ❌ NOT FOUND as span attributes (only `sre.result.latest_tool/source`, and only for the single latest call — ties to §6's finding of no per-call span) |
| `tool.duration_ms` | ❌ NOT FOUND as a span attribute (only in `tool_history` state/logs) |
| `tool.status` | partial — `sre.result.latest_ok`, latest-call-only |
| `investigation_completeness` | ❌ NOT FOUND anywhere in the codebase as a tracked field at all — worth confirming with you whether this concept currently exists under a different name, or doesn't exist yet |

**High-cardinality risk:** `sre.cluster`/`sre.namespace`/`sre.pod` are technically unbounded but practically bounded by real infra count — low real-world risk.

**Sensitive-data check:** none of the current attributes contain secrets, raw logs, prompts, or full model responses — the codebase already respects your "never trace these" rule. No violation found.

**Recommendation:** mostly **ALREADY IMPLEMENTED** under different names; **IMPLEMENT NOW** for the 3 real gaps once §6/§7 land naturally (`mcp.tool`, `mcp.server`, `tool.duration_ms`, `tool.status` become trivial additions to the new MCP-call span from §6); `agent.name`/`agent.version` are a 2-line addition; `investigation_completeness` needs a decision from you on whether it's a new concept to define or an existing one under another name.

---

## 9. OTLP wholesale architecture migration

**Current:** direct-to-Google-API per signal — `CloudTraceSpanExporter` for traces, `cloud_logging.Client` for logs, no metrics pipeline at all.

**Google capability (VERIFIED):** OTLP log and metric ingestion via Telemetry API are both GA (direct-fetch confirmed, reproduced twice for logs). Collector is optional, not required.

**Benefit:** one ingestion protocol/endpoint for all 3 signal types, one IAM role instead of per-API permissions.

**Risk:** Agent Gateway compatibility with `telemetry.googleapis.com` is **UNVERIFIED** — Google's docs don't address egress-allowlist/registry architectures at all, and #139 is direct proof this exact class of interaction is where things break in this environment. Migrating a path just fixed after a multi-week bug to an entirely new, unproven path is real regression risk against no demonstrated current problem.

**Recommendation: DO NOT IMPLEMENT NOW.** Matches your own stated rule — no clear advantage demonstrated, current path works and is simpler. Revisit only if a new signal type (e.g. §5's metrics) is added and needs a transport decision anyway, and only after that scoped experiment proves Agent Gateway compatibility.

---

## 10. Cloud Trace attribute limits

**Current:** no span currently approaches any limit (span attribute counts are all small, per §8's list).

**Google capability (VERIFIED, direct neutral-prompt fetch of `docs.cloud.google.com/trace/docs/quotas`):** your stated numbers are current — 1,024 attributes/span, 512-byte attribute keys, 65,532-byte attribute values, 1,024-byte span names, 256 events/span (Trace API). A separate, larger set of limits applies specifically to OTLP/Telemetry-API ingestion (1,024 resource attributes, 8,192 attrs/ResourceSpan, 128 Links/span, 8,192-byte schema URL) — relevant only if/when §9 or §5 are adopted.

**Recommendation: ALREADY IMPLEMENTED / no action** — current usage is well within limits regardless of transport choice; the real work is the schema gap in §8, not headroom.

---

## Final architecture (unchanged transport, gaps closed)

```
PagerDuty → Agent Engine → LangGraph nodes → Gemini + Agent Gateway + MCP → Evidence (GCS)
                    │              │                  │            │           │
                    └────────┬─────┴────────┬─────────┴─────┬──────┴─────┬─────┘
                              trace_id (OTel span context, originates at the outer
                              "sre_agent.investigation" span in main.py) +
                              run_id (state.py:make_run_id(), generated once at
                              get_initial_state(), carried explicitly in AgentState)
                                          │
                     ┌────────────────────┼─────────────────────┐
                     ▼                    ▼                     ▼
              Cloud Trace          Cloud Logging           (no native metrics
         (CloudTraceSpanExporter) (cloud_logging.Client,   pipeline today —
                                    _use_grpc=False,        Terraform log-based
                                    HTTP_JSON registry)     metrics only)
```

`run_id` is explicit application state, propagated by value into every node, every MCP call, every GCS write. `trace_id` is implicit OTel context, read back via `get_trace_id_hex()` wherever it needs to be logged for correlation — never stored in `AgentState` itself. No architecture change recommended for this diagram; the gaps are instrumentation coverage (§3, §6, §7, §8), not transport (§9).

---

## Phased plan

**Phase 0 (this document):** validation only. Complete, except §8's `investigation_completeness` question needs your input.

**Phase 1 — lowest-risk, propose next (all SAFE, all additive/isolated):**
- §3 telemetry fail-open fix in `main.py` (real bug)
- §1 `register_endpoints.py` landmine + test gap + stale docs
- §6 MCP call-level self-instrumented spans
- §7 GCS evidence-store spans
- §2 the 5 broken alerts (separately tracked, pending your go-ahead)

**Phase 2 — production monitoring:**
- §4 structured log correlation IDs
- §8 attribute schema cleanup/renames once §6/§7 land
- Broader actionable-alert review beyond the 5 already broken (your prompt's full list — failure/latency/throttling/backpressure alerts — not separately scoped in this pass; would need its own review)

**Phase 3 — architecture, conditional only:**
- §5 OTel Metrics API, scoped single-metric experiment, proving Agent Gateway compatibility first
- §9 full OTLP consolidation — only if Phase 3's experiment proves both compatibility and a real benefit; no blanket migration

---

## Open item — needs your input, not guessed

§8: `investigation_completeness` doesn't exist anywhere in the current codebase under any name I could find. Is this a new concept to define, or does it map to something already tracked (e.g. evidence_count vs. some target, or confidence_band)?
