# Cloud Trace content capture — decision record

**Status:** Disabled (config) + custom span content leaks removed (code).
**Last verified:** 2026-08-13. **Tracking:** issue #76 (closed), issue #130
(separate, open — Cloud Trace export itself is broken/unreliable).

## What this is

`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` is a standard OpenTelemetry
GenAI semantic-convention flag. When `true` and the matching auto-instrumentation
library is installed and active, it includes the *actual text* of every prompt
sent to Gemini and every response received — not just token counts or timing —
in Cloud Trace spans. Because this agent builds its prompts from real
Kubernetes evidence (pod logs, event text, error messages from the customer's
own cluster), that real data could flow into Cloud Trace if this path were live.

## What happened

The flag was set `true` at 100% trace sampling
(`OTEL_TRACES_SAMPLER_ARG = "1.0"`) when telemetry was first wired up
(`iac/agent/agent_engine.tf`, commit `aea45c3e`, 2026-07-13). No document
anywhere in this repo mentioned or scoped this — it was live and undocumented
for about a month before being caught in the 2026-08-09 full-repo audit
(issue #76).

## Decision (2026-08-12)

**Disabled.** `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT = "false"`.
`OTEL_TRACES_SAMPLER`/`_ARG` are **unchanged** (still 100% sampling) — that
controls how *often* a trace is recorded, a separate concern from what a trace
*contains*; normal performance/timing visibility was explicitly kept.

The flag was disabled as a deliberate safety decision regardless of whether it
was actively capturing anything at the time (see "What was actually proven"
below) — undocumented, live-since-day-one settings with this kind of
compliance exposure don't get evaluated case-by-case on whether they happened
to be doing damage; they get turned off and recorded.

## What was actually proven, and what was not

**Proven, with direct evidence:**

- The `opentelemetry-instrumentation-google-genai` package — the library that
  actually reads this flag and performs the automatic prompt/response capture
  — is **not installed** (`agent/requirements.txt` only declares
  `opentelemetry-api`, `opentelemetry-sdk`,
  `opentelemetry-exporter-gcp-trace`). Confirmed live via Cloud Logging on a
  real investigation run (`run_20260813_045745_ainm`): `WARNING: telemetry
  enabled but proceeding without Google GenAI instrumentation, because
  opentelemetry-instrumentation-google-genai has not been installed`.
- Because that library was never installed, **automatic prompt/response
  capture via the OTel GenAI flag was never actually exercised** — the flag
  had nothing to act on. This does not mean the flag was safe to leave
  undocumented and enabled (a future dependency change could install that
  package and silently activate it); it means the specific compliance
  exposure the flag implies was not demonstrated to have occurred.
- A follow-up review (2026-08-13, prompted by direct user pushback on this
  document's first draft, which is being corrected here) found that this
  repo's **own custom OpenTelemetry code** — not the flag, not a third-party
  library — carried real investigation content into spans through three
  separate paths, independent of the flag entirely:
  1. `agent/main.py`'s `"sre.query": query[:250]` span attribute — the raw
     user query text.
  2. `agent/otel.py`'s `trace_node()` exception handling —
     `span.record_exception(exc)`, `Status(..., str(exc))`, and
     `"sre.node.error": str(exc)` all sent the exception's own message text
     into the span.
  3. Both spans relied on `start_as_current_span()`'s default
     `record_exception=True`/`set_status_on_exception=True` — OTel's own
     framework auto-captures an exception message on re-raise, independent of
     (2)'s explicit handling. Caught by a regression test failing on its first
     run, not caught by code review alone.

  All three fixed in PR #133 (commit `a64fb016bdd76143c5e6eff9c202450661074e8f`,
  deployed via squash-merge commit `6cf9c743c9c6bb1406f2375357141d34a0980b1e`,
  deployment run `31679550922`). `sre.query` removed entirely; exception
  handling now records only the exception's TYPE (`sre.node.error_type`, e.g.
  `"ValueError"`), never the message. 3 new regression tests use a real
  in-process OTel `TracerProvider` + `InMemorySpanExporter` to prove a unique
  secret marker never reaches any span attribute, status, or event, on either
  the query path or the exception path.
- **Live configuration verified directly on the deployed resource**
  (Vertex AI REST API, not just Terraform state), immediately after the PR
  #133 deploy:
  ```
  OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT = false
  OTEL_TRACES_SAMPLER_ARG = 1.0
  ```
  Agent Engine ID confirmed unchanged before and after
  (`7801582006105538560`).
- **Cloud Trace read access** (`gcloud projects get-iam-policy`, live query,
  2026-08-12): only two principals can view traces on this project — the
  agent's own identity holds `roles/cloudtrace.agent` (write-only, no read
  permission), and the project owner (`roles/owner`, full access). No broader
  team or service account exposure.
- **Retention / cleanup of already-captured content**: confirmed via
  [Cloud Trace's official quota/retention docs](https://cloud.google.com/trace/docs/quotas) —
  30-day automatic retention, no manual deletion API exists (confirmed against
  the [Cloud Trace API v2 reference](https://cloud.google.com/trace/docs/reference/v2/rest) —
  only `traces.batchWrite` and `traces.spans.createSpan`, no delete method).

**NOT proven — explicitly not claimed:**

- **A working, populated Cloud Trace span was never inspected.** A real live
  investigation was run specifically to do this (`run_20260813_045745_ainm`),
  but it produced `trace_id=""` in its own log entry, and Cloud Trace itself
  returned zero spans for that run's time window (checked directly, both a
  narrow window and a wider one allowing for export delay). This project's
  entire Cloud Trace history has only 2 traces total, both from 2026-08-09,
  one span each — unrelated to this run.
- This means end-to-end Cloud Trace verification — actually seeing a real
  span, with the fixes in this PR reflected in it — **could not be performed**
  and is not claimed here. Cloud Trace export itself appears broken or
  unreliable for this deployment; that is tracked separately as **issue
  #130**, and is a different, real problem from the content-capture question
  this document covers. Fixing #130 is required before anyone can visually
  confirm (rather than infer from code + live-config evidence) that a real
  span in Cloud Trace contains metadata only.

## If this is revisited

Re-enabling full content capture is a real option if deep prompt/response
debugging is ever needed (e.g. investigating a bad RCA), but should be a
deliberate, time-boxed, documented decision each time — not left permanently
on. If re-enabled, also revisit Cloud Trace IAM (who can view) at that time,
since it changes what's exposed even if only briefly. It would also require
actually installing `opentelemetry-instrumentation-google-genai` (see above) —
today, re-enabling just the flag alone would still not activate automatic
content capture.
