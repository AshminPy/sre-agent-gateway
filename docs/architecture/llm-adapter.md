# LLM Adapter / Model Portability

> **Implementation Status:** IMPLEMENTED — adapter abstraction + fake-provider contract tests. A second real vendor is NOT implemented (no target/credentials specified — see below).
> **Last Verified:** 2026-09-08 (Section 9 of the new-assignment expansion work)
> **Source of truth:** `agent/llm/base.py` (`LLMClient` ABC), `agent/llm/registry.py` (resolution), `agent/llm/gemini_adapter.py` (the only real adapter), `tests/test_llm_contract.py` (fake-provider proof)
> **Owner:** SRE Agent platform team

## What's genuinely config-only today

Switching between **Gemini profiles** (`gemini-2.5-flash` ↔ `gemini-2.5-pro`, or a future `gemini-3-*`) is a real, one-variable switch: `LLM_PROFILE`/`GEMINI_MODEL` (Terraform var → env var, `agent/llm/registry.py:_resolve_profile()`). No code change, no redeploy of application logic — same class, different `model` string.

This is **not** the same claim as "any model is a config switch" — see below.

## What's real but NOT config-only: a second vendor

`agent/llm/base.py`'s `LLMClient` ABC is a genuine abstraction — confirmed via grep that `agent/llm/gemini_adapter.py` is the ONLY file importing the Gemini/Vertex SDK anywhere in `agent/`. `tests/test_llm_contract.py`'s `FakeLLMClient` proves the registry, capability validation, and usage-accounting contract all work for a provider that isn't Gemini at all — no network, no real credentials, deterministic.

**Adding a real second vendor (e.g. Anthropic, OpenAI) is bounded, concrete work, not implemented here because no target/account/access was specified** (per this section's own instruction: don't invent a preference or credentials). The actual remaining work, in order:

1. **Write the adapter module** (`agent/llm/<vendor>_adapter.py`) implementing every `LLMClient` abstract method — `llm()`, `llm_json()`, `get_session_usage()`, `reset_session()`, `count_tokens()`, `count_json_request_tokens()`, `max_context_tokens()`. `gemini_adapter.py` is the concrete template; the fake in `test_llm_contract.py` is the minimal-conformance template.
2. **Credentials + connectivity**: a real API key or workload-identity path for that vendor, and confirmation the Agent Gateway's egress path actually supports it (Gemini's Vertex AI traffic goes through IAP REQUEST_AUTHZ with no content inspection needed on that path today — a different vendor's endpoint would need the same egress-authorization question answered explicitly, not assumed).
3. **Pricing/accounting**: that vendor's real per-token pricing, wired the same way `GEMINI_PRICE_INPUT`/`GEMINI_PRICE_OUTPUT` are today (Terraform var → env var → `_calculate_cost()`), never a guessed/copied rate.
4. **Capability declaration**: does it actually support structured JSON output and tool-calling the way this agent's prompts assume (`CAPABILITY_STRUCTURED_OUTPUT`, `CAPABILITY_TOOL_CALLING`)? Confirm against that vendor's own docs before declaring it — a wrong declaration here would pass `validate_capabilities()` while silently producing malformed output at runtime.
5. **Register it**: one line, `register_adapter("<prefix>", lambda profile: VendorAdapter(model=profile))`, mirroring `_register_gemini()`.
6. **Contract tests against the REAL adapter** (not just the fake): at minimum, one test proving a real `llm_json()` call round-trips through the exact prompt shapes `agent/nodes/*.py` actually sends (schema-heavy, e.g. `RCA_BUILDER_USER`'s multi-field JSON) — the fake proves the *interface* works; this proves *this specific vendor's model* can actually satisfy it.
7. **A real, live, single-investigation test** against that vendor before calling it production-ready — same "CI-green is not proof" standard this repo applies everywhere else.

None of this is started. Nothing here should be read as "close to done" — it's the concrete checklist for when a real target is specified, not partial progress toward one.

## What this section explicitly does NOT claim

- **"Gemini-to-Gemini switching proves universal portability."** It doesn't — it proves the *registry* and *config plumbing* are provider-neutral; it says nothing about whether a genuinely different vendor's API shape, auth model, or capability set would actually fit this same abstraction without adapter-side surprises. `agent/llm/registry.py`'s own docstring already says this plainly.
- **Silent vendor/model fallback on failure.** Confirmed via audit: no code path substitutes a different vendor or model during a failure. `GeminiAdapter._call_model()` only ever retries the *same* model (429/5xx, bounded, see below); an unmatched `LLM_PROFILE` raises `ValueError` rather than defaulting to any adapter.

## Reliability: retry behavior (Section 9, 2026-09-08)

`GeminiAdapter._call_model()`'s retry loop is bounded (3 attempts max, linear 30s/60s backoff, never unbounded/queued) and now covers 429 **and** 500/503/504 — previously only 429 retried; any 5xx failed the whole investigation on its first occurrence. A non-retryable error (e.g. a 400) still fails immediately with no wait.

## Concurrency isolation (Section 9, 2026-09-08)

`agent/llm/registry.py` caches ONE adapter instance process-wide, reused by every investigation a warm Agent Engine instance handles. Session/usage counters (token counts, cost, call count) now live behind a `contextvars.ContextVar`, not plain instance attributes — proven via a real multi-threaded test (`tests/test_gemini_adapter_concurrent_sessions.py`) that two concurrent investigations sharing that one instance cannot cross-contaminate each other's accounting. This was a real, demonstrated gap before the fix (confirmed via audit, independently corroborated by `docs/architecture/agent-engine.md`'s own pre-existing "flag as UNKNOWN, not independently load-tested" note), not a hypothetical one.

## Capacity / admission control (Section 9, 2026-09-08)

`max_instances=10` is now set explicitly in `iac/agent/agent_engine.tf` (previously unset — live state showed `0`, not the ~100 platform default this repo's docs previously assumed). This is the platform's own native admission mechanism — deliberately **not** a custom in-process semaphore, per this section's own instruction ("a per-process limiter is not a global quota controller"). See `docs/governance/capacity.md` for the honest caveat: `10` is a deliberate starting cap, not derived from a measured concurrent load test.

**Not done in this pass, disclosed not hidden:** a genuine concurrent-load capacity report (tested concurrency, latency distribution, error rate under real parallel load) — the assignment explicitly warns against inferring this from a sequential campaign. That is separate, real load-testing work, scoped for when it's actually run, not fabricated here.
