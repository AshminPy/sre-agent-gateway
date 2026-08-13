# Cloud Trace content capture — decision record

**Status:** Disabled. **Last verified:** 2026-08-13. **Tracking:** issue #76.

## What this is

`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` is a standard OpenTelemetry
GenAI semantic-convention flag. When `true`, it includes the *actual text* of
every prompt sent to Gemini and every response received — not just token counts
or timing — in Cloud Trace spans. Because this agent builds its prompts from
real Kubernetes evidence (pod logs, event text, error messages from the
customer's own cluster), that real data would flow into Cloud Trace.

## What happened

The flag was set `true` at 100% trace sampling
(`OTEL_TRACES_SAMPLER_ARG = "1.0"`) when telemetry was first wired up
(`iac/agent/agent_engine.tf`, commit `aea45c3e`, 2026-07-13). No document
anywhere in this repo mentioned or scoped this — it was live and undocumented
for about a month before being caught in the 2026-08-09 full-repo audit
(issue #76).

## Decision (2026-08-12)

**Disabled.** `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT = "false"`.
Cloud Trace keeps token counts, latency, and span structure for debugging —
just not the raw prompt/response text. `OTEL_TRACES_SAMPLER`/`_ARG` are
**unchanged** (still 100% sampling) — that controls how *often* a trace is
recorded, a separate concern from what a trace *contains*; normal
performance/timing visibility was explicitly kept.

## Verification performed before and after the fix

- **Custom span attributes** (`agent/otel.py`'s `set_span_attributes`,
  `_state_attrs`, `_result_attrs`, `_emit_gen_ai_span`): read directly, confirmed
  metadata-only (node name, run_id, cluster/namespace/pod names, token counts,
  tool names, evidence IDs/counts, error counts) — no prompt/response text set
  manually anywhere in this repo's own code. The content capture was entirely
  from the OTel auto-instrumentation flag, not a second code path.
- **Application logs (Cloud Logging), a separate system from Trace**: found and
  fixed two narrower instances in `agent/llm/gemini_adapter.py`'s `llm_json()`
  JSON-parse-failure paths that logged up to 200-300 chars of the model's raw
  response text on error. Now log length + parser error position only.
- **Cloud Trace read access** (`gcloud projects get-iam-policy`, live query,
  2026-08-12): only two principals can view traces on this project --
  the agent's own identity holds `roles/cloudtrace.agent` (write-only, no read
  permission), and the project owner (`roles/owner`, full access). No broader
  team or service account exposure.
- **Retention / cleanup of already-captured content**: confirmed via
  [Cloud Trace's official quota/retention docs](https://cloud.google.com/trace/docs/quotas) --
  30-day automatic retention, no manual deletion API exists (confirmed against
  the [Cloud Trace API v2 reference](https://cloud.google.com/trace/docs/reference/v2/rest) --
  only `traces.batchWrite` and `traces.spans.createSpan`, no delete method).
  Traces created before ~2026-07-14 have already auto-expired; traces from
  roughly the last 30 days may still exist and will expire on their own
  individual 30-day schedules -- nothing can accelerate this, and access is
  already minimal (see above) while it does.
- **Deployed live validation**: a real investigation was run after deploying
  this change, and the resulting Cloud Trace spans were inspected directly to
  confirm they contain metadata only -- see the commit/PR for the real trace ID
  and inspection result.

## If this is revisited

Re-enabling full content capture is a real option if deep prompt/response
debugging is ever needed (e.g. investigating a bad RCA), but should be a
deliberate, time-boxed, documented decision each time -- not left permanently
on. If re-enabled, also revisit Cloud Trace IAM (who can view) at that time,
since it changes what's exposed even if only briefly.
