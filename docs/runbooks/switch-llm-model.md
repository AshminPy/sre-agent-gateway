# Runbook: Switching the LLM / Model

> **Last Verified:** 2026-09-08 (Section 11 of the new-assignment expansion work) · **Owner:** SRE Agent platform team

Full architecture/design context lives in [LLM Adapter / Model Portability](../architecture/llm-adapter.md) — this runbook is the short, operational "how do I actually do it" version.

## Switching between supported Gemini profiles (config-only, no code change)

**Supported today:** `gemini-2.5-flash`, `gemini-2.5-pro` — both declared in `agent/llm/gemini_adapter.py`'s `_VERTEX_INPUT_TOKEN_LIMIT_FALLBACK` table and both known to support structured JSON output + tool-calling (this agent's two hard requirements, `CAPABILITY_STRUCTURED_OUTPUT`/`CAPABILITY_TOOL_CALLING`).

1. **Configure**: set `gemini_model` in `iac/agent/terraform.tfvars` (gitignored — real production value lives here, never in a committed default) to the target profile string.
2. **Pricing — update in the SAME change**: `gemini_price_input_per_1m`/`gemini_price_output_per_1m` (same file) must match the new profile's real published rate. Nothing derives one from the other automatically — a stale price after a model switch silently reports the WRONG cost, not an error.
3. **Apply**: `terraform apply` in `iac/agent` — this rewrites the `GEMINI_MODEL`/`LLM_PROFILE` env vars on the Agent Engine's `deployment_spec`, no image rebuild.
4. **Validate**: run one real investigation (`scripts/smoke_test.sh` or a manual `SREAgent.query(...)` call) and confirm the response actually used the new model — check the RCA report's `Model` line (`agent/main.py`'s `_build_rca_report`) and the structured `sre_agent_run` log's cost fields look sane for the new pricing.
5. **Rollback**: revert `gemini_model`/the two price vars in `terraform.tfvars`, `terraform apply` again. No data migration, no code involved — same mechanism as the switch itself, in reverse.

## Adding a genuinely new profile string (still Gemini, e.g. a future `gemini-3-*`)

Same steps above, PLUS: add the new model's real documented input-token limit to `_VERTEX_INPUT_TOKEN_LIMIT_FALLBACK` in `agent/llm/gemini_adapter.py` first (`max_context_tokens()` raises `RuntimeError` rather than guessing for an unlisted model — this is a deliberate fail-closed guard, not a bug to work around). Confirm the new model still supports structured output/tool-calling per its own release notes before assuming it does.

## Switching to a genuinely different vendor (Anthropic, OpenAI, etc.)

**Not currently supported — this is real, unstarted work, not a configuration switch.** See [LLM Adapter / Model Portability](../architecture/llm-adapter.md)'s "What's real but NOT config-only" section for the concrete 7-step checklist. Do not attempt a shortcut version of this by pointing `LLM_PROFILE` at an unregistered vendor string — `agent/llm/registry.py`'s `get_client()` raises a clear `ValueError` rather than silently falling back to anything.

## Cross-vendor limitations to know before starting that work

- **Egress authorization is vendor-specific, not assumed transferable.** Gemini's Vertex AI traffic reaches Google's own endpoint through IAP REQUEST_AUTHZ with no content-inspection step needed on that path. A different vendor's API endpoint is a genuinely different egress target — whether Agent Gateway's existing authorization model covers it needs to be verified explicitly for that vendor, never assumed identical.
- **Capability declarations are not portable.** `CAPABILITY_STRUCTURED_OUTPUT`/`CAPABILITY_TOOL_CALLING` describe what THIS agent's prompts assume the model can do — a different vendor's model may support a differently-shaped structured-output mode (e.g. function-calling schemas that don't map 1:1 to Gemini's), which could pass a naive capability check while still producing malformed output against this agent's specific prompt schemas.
- **Silent fallback is explicitly not supported, by design** — confirmed via audit: no code path substitutes a different vendor/model during a failure. Any future fallback must be an explicit, pre-configured, reported decision, never an automatic one (Section 9's own requirement).

---

**Related pages:** [LLM Adapter / Model Portability](../architecture/llm-adapter.md) · [Operate](operate.md)
