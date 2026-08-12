# Google Cloud Support Case Draft — Reasoning Engine query request timeout (~300s), undocumented

> Draft for a Google Cloud support case. Not yet submitted. Compiled 2026-08-12 from this
> repo's own investigation records (GitHub issues #103, #74; `agent/nodes/loop_controller.py`,
> `agent/llm/gemini_adapter.py`) — every quote/error/timestamp below is copied verbatim from
> real logs, Cloud Trace, and CI run output, not reconstructed from memory. Real project/resource
> identifiers are included directly in this draft per explicit instruction — repo is private.

---

## Subject

Deployed Reasoning Engine queries fail with a ~300-second "stream timeout" that we cannot find
documented anywhere — need the exact value/scope confirmed, whether it's configurable, and
whether it depends on the LLM backend or is a platform-level Agent Engine limit.

## Summary

We operate a Vertex AI Agent Engine (Reasoning Engine, LangGraph-based SRE investigation agent)
calling Gemini 2.5 Pro via `google.genai.Client(vertexai=True)`. On 2026-08-11, three consecutive
live investigations failed with an identical client-side error after ~300 seconds, even though
our own application code sets no timeout anywhere. Investigation showed the backend kept running
well past that point and completed normally 16–345 seconds *later* — the caller had already
received a hard failure with zero output by then.

We could not find this timeout documented in any Reasoning Engine / Agent Engine API reference we
reviewed (list below). We need to know: the exact value and scope of this limit, whether it's
raisable, and whether the same limit would apply if we used a different LLM behind the same
Reasoning Engine (we believe it's a platform-level limit independent of the model, based on our
own client-code inspection below, but would like Google to confirm).

## Environment

- **Product:** Vertex AI Agent Engine (Reasoning Engine)
- **Project:** `sreagent-t2-demo`
- **Reasoning Engine resource:** `projects/sreagent-t2-demo/locations/us-central1/reasoningEngines/7801582006105538560`
- **Region:** `us-central1`
- **Deployment config:** `min_instances=2`, LangGraph-based agent, `google-genai` SDK, model
  `gemini-2.5-pro`
- **Client:** our own `invoke_agent.py`, calling `agent.query_reasoning_engine(request={"name": ..., "input": ...})` — synchronous, non-streaming call
- **Repo (private, can share access on request):** `AshminPy/sre-agent-gateway`

---

## Problem — client receives a hard failure at ~300s while the backend is still running

**Error received (verbatim, three separate occurrences, 2026-08-11):**

```
ERROR [300.5s]: 400 Reasoning Engine Execution failed.
Please refer to our documentation (https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/troubleshooting/use) for checking logs and other troubleshooting tips.
Error Details: stream timeout
```

(Two of the three occurrences: `300.5s`, `300.5s`. Third: `300.6s`. All three from independent
CLI invocations, not a single retried request.)

**We confirmed this is NOT a timeout our own code sets.** We searched `invoke_agent.py` (our
only client) for any `timeout=` parameter — none exists. The call itself:

```python
response = agent.query_reasoning_engine(
    request={"name": _resource_name(), "input": scenario}
)
```

No local deadline, no wrapping `asyncio.wait_for`, no `httpx`/`requests` timeout anywhere in the
call path.

**We also checked the SDK's own generated client for a built-in default deadline, to rule that
out as a separate explanation.** In our installed `google-cloud-aiplatform` (1.148.1),
`reasoning_engine_execution_service`'s `transports/base.py` sets `default_timeout=None` on every
wrapped RPC method (12 occurrences checked) — the generated client itself does not carry a
hardcoded timeout for this call either. We list this because "the SDK has its own hidden
default" was a real alternative explanation we wanted to rule out with evidence, not assume away.

Given both of the above, our current best explanation is that this is enforced somewhere between
the client and the deployed engine — most likely Agent Engine's own request-handling layer, since
the error text itself (`"400 Reasoning Engine Execution failed... Error Details: stream
timeout"`) reads as a platform-generated wrapper error rather than something a client library
would raise. **We are treating this as our working hypothesis, not a confirmed fact** — we have
not ruled out an intermediate layer we're not aware of, and are asking Google to confirm exactly
where and how this limit is enforced (see Questions below).

**We have direct evidence the backend kept running after the client failed.** Using Cloud Trace
(`gen_ai.chat gemini-2.5-pro` spans, `langgraph.*` node spans) and our own structured logs, we
traced two of the three failed client calls to completion server-side, well after the client had
already reported failure:

| Attempt | Client result | Backend actually finished | Gap |
|---|---|---|---|
| Run `run_20260811_160055_xqhp` (imagepull-pod) | `stream timeout` @ 300.5s | Completed server-side ~316s after start (confirmed via a `Failed to write observability log` + completion-shaped log entry, same signature as a normal successful run) | Backend outlived client failure by ~16s |
| Run `run_20260811_161042_mzpx` (crashloop-pod) | `stream timeout` @ 300.5s | Completed server-side ~645s after start (confirmed via Cloud Trace, trace `828b01ce4818b6a6294d8872b1299882`, 24 spans, `sre_agent.investigation` span duration `644.86s`) | Backend outlived client failure by **~345s** |

In both cases, real, useful RCA output existed server-side that the caller never received — the
client had already torn down and reported a bare error.

A third attempt (`run_20260811_160048_fezc`, oomkilled-pod) instead hit a genuine backend error —
`429 RESOURCE_EXHAUSTED` from Vertex AI at ~575s — a different failure mode we are not asking
about here (that's a quota/rate-limit issue on our project, being handled separately).

**What was slow, for context (not the subject of this case, but relevant to reproduction):** real
per-call Gemini latency was elevated 7–15x that day (34–69s per model call, vs. 1.8–15.9s in
normal runs measured the same morning) — this is what pushed total wall-clock time past whatever
the request boundary is. We are not asking Google to explain that specific slowdown here; we're
asking about the boundary itself, since even a legitimately slow-but-successful run gets killed
by it with no partial output delivered.

---

## Documentation we reviewed (fetched directly, 2026-08-12)

1. `docs.cloud.google.com/vertex-ai/generative-ai/docs/reference/rest/v1/projects.locations.reasoningEngines/streamQuery`
2. `docs.cloud.google.com/agent-builder/agent-engine/troubleshooting/code-execution`
3. `docs.cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/troubleshooting/use` (the exact URL our own error message points to)
4. `docs.cloud.google.com/agent-builder/agent-engine/use/custom`

**None of these pages document a specific timeout duration for `query`/`streamQuery` requests.**
We searched specifically for "timeout," "300," and "stream timeout" on each. We found general
troubleshooting guidance (checking logs, Cloud Trace) but no stated numeric limit, no statement
of whether it's configurable, and no statement of whether it depends on the model/LLM backend
versus being a property of the Reasoning Engine request/response cycle itself.

**We believe this is a genuine documentation gap, not something we overlooked** — happy to be
corrected with a direct link if one exists.

## Questions for Google

1. What is the exact, documented request timeout for a synchronous `query`/`streamQuery` call to
   a deployed Reasoning Engine? Is it exactly 300 seconds, or does our observed `300.5`–`300.6s`
   reflect e.g. a 300s server-side limit plus client-side overhead?
2. Is this timeout configurable or raisable — per-project, per-engine, or per-request? If so, how?
3. Is this limit a property of Agent Engine's request-handling layer (i.e., would it apply
   identically regardless of which LLM the agent calls), or can it vary by model/backend? We
   believe it's model-agnostic based on our own client-code inspection (no timeout set by us,
   error text is the platform's own), but would like this confirmed.
4. Is there a supported pattern for long-running agent investigations that might legitimately
   exceed this boundary — e.g. an async/polling completion API, a webhook/callback on completion,
   or a way to keep the stream alive past 300s — so a slow-but-eventually-successful run doesn't
   result in total data loss for the caller?
5. Separately: is `300.5`–`300.6s` (consistently ~0.5s over an exact 300s mark) expected/known
   client-observed jitter, or could it indicate the real server-side cutoff is slightly different
   from exactly 300s?

---

## What we are NOT reporting as a bug (ruled out, don't need Google's help here)

- The Gemini call latency slowdown itself (7–15x normal) — being tracked separately as a
  possible quota/capacity issue on our project, not asking Google to explain root cause here.
- The `429 RESOURCE_EXHAUSTED` error on the third attempt — a distinct, already-understood
  quota/rate-limit failure mode, not part of this case.
- A pre-existing, separate bug in our own observability logging (`Failed to write observability
  log: 403 ... unregistered in the Agent Registry`) — unrelated to this timeout, tracked
  internally.

## Current mitigation on our side

We've added an application-level safety budget (`agent/nodes/loop_controller.py`) that stops our
LangGraph agent from *starting* another expensive step once elapsed time leaves too little
headroom before this boundary — so instead of a bare client failure with zero output, the caller
gets a truthful partial result. This is a workaround, not a fix: it cannot interrupt a single
model/tool call already in progress, so a call that starts just under our internal budget and
itself runs long can still hit the real platform boundary with no output delivered. This is why
we're asking Google directly rather than only mitigating client-side.

## Attachments to prepare before filing

- [ ] Full CLI output for all three failed attempts (already captured in this repo's issue #103)
- [ ] Cloud Trace export for trace `828b01ce4818b6a6294d8872b1299882` (the 645s completion case)
- [ ] `agent/nodes/loop_controller.py` (current, with our safety-budget mitigation)
- [ ] Console screenshots of the Cloud Trace timeline for the affected runs (not captured in this
      repo — take fresh screenshots before filing)
