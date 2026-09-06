# Switching the LLM / Model

> **Implementation Status:** IMPLEMENTED for changing the Gemini model. Provider-neutral switching (e.g. to a non-Gemini model) requires new code, not just a variable change.
> **Last Verified:** 2026-09-06 — `iac/agent/variables.tf`, `iac/agent/agent_engine.tf`, `agent/llm/registry.py`, `agent/llm/gemini_adapter.py`
> **Owner:** SRE Agent platform team.

## The exact variable

`var.gemini_model` (`iac/agent/variables.tf`), default `"gemini-2.5-flash"`. This becomes **two** environment variables on the deployed Agent Engine (`iac/agent/agent_engine.tf`):

```hcl
GEMINI_MODEL = var.gemini_model   # legacy name, still read
LLM_PROFILE  = var.gemini_model   # provider-neutral selector (issue #63)
```

`agent/llm/registry.py::_resolve_profile()` reads `LLM_PROFILE` first, falling back to `GEMINI_MODEL` if unset — both point at the same Terraform variable today, so in practice there is exactly one value to change.

**Live deployment note**: the Terraform variable's own code default is `gemini-2.5-flash`, but both CI workflows (`.github/workflows/terraform-plan.yml`, `terraform-apply.yml`) pass an explicit `-var="gemini_model=gemini-2.5-pro"` — the real deployed model is **`gemini-2.5-pro`**, not the code default. Check the CI workflow files, not just `variables.tf`, before assuming what's actually live.

## Supported values today

Only Gemini models. `agent/llm/registry.py`'s adapter dict has exactly one entry, registered for any `LLM_PROFILE` string starting with `gemini-` (the whole profile string doubles as the literal model name the Gemini API expects — e.g. `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.0-flash`). Setting `LLM_PROFILE`/`gemini_model` to anything not matching a registered prefix raises `ValueError` at first use, not silently falling back.

**Switching to a non-Gemini provider (e.g. Claude, GPT) is NOT a config change** — it requires:
1. Writing a new adapter module (subclass of `agent.llm.base.LLMClient`) with its own credentials and pricing source.
2. `register_adapter("<new-prefix>", factory)` in `agent/llm/registry.py`.
3. Only then does changing `LLM_PROFILE` become a real, code-free switch. The LangGraph workflow (`agent/nodes/*`, `agent/main.py`) never changes for this — it only imports from `agent.llm`'s facade.

## Cost/pricing must be updated together

`var.gemini_price_input_per_1m` (default `1.25`) and `var.gemini_price_output_per_1m` (default `10.0`) are **Gemini 2.5 Pro's real published per-1M-token rates** (verified against `cloud.google.com/vertex-ai/generative-ai/pricing` 2026-08-10), used by `agent/llm/gemini_adapter.py` to compute `estimated_cost_usd` on every investigation. **Nothing derives these from `gemini_model` automatically** — if you change the model, you must update both pricing variables to match the new model's real published rate in the same change, or every investigation's cost estimate will be silently wrong (not zero — an actively misleading wrong number for a different model's price).

The pricing comment also notes: this is a flat rate valid for prompts ≤200K tokens; Gemini's real pricing is tiered above that. Considered acceptable because investigations are hard-capped (5 loop steps, evidence compressed before every prompt) and don't realistically approach 200K tokens in one call — re-evaluate this assumption if that changes.

## Deployment impact

Changing `gemini_model` is a plain environment-variable change on the Agent Engine resource — no image rebuild, no MCP redeploy. A normal `terraform apply` (or a PR through the CI pipeline) picks it up.

## Validation steps after switching

1. `terraform plan` — expect only the Agent Engine's env vars to change (`GEMINI_MODEL`, `LLM_PROFILE`), nothing else.
2. Run a real investigation: `python invoke_agent.py --scenario imagepull --verbose` (or `make smoke`). Confirm the response completes and `observability.estimated_cost_usd` is a plausible non-zero number for the new model's real rate — a `$0.00` or an obviously-wrong value means the pricing variables weren't updated together with the model.
3. Check Vertex AI quota for the new model specifically (Console → IAM & Admin → Quotas, filter `aiplatform.googleapis.com`) — different models can have very different default per-minute quotas; a low quota surfaces as `429 RESOURCE_EXHAUSTED` during `make smoke`, which looks like a broken deploy but isn't (see the README's Prerequisites section).
4. If the model's context window or output-token behavior differs materially from Gemini 2.5 Pro/Flash, re-check `rca_builder`'s `max_tokens` setting (`agent/llm/gemini_adapter.py`) — this was previously tuned specifically for truncation issues on the biggest-output-schema call in the codebase.

---

**Related pages:** [Agent Engine](../architecture/agent-engine.md) · [Cost Management](../governance/cost-management.md) · [Updating the Agent / CI/CD](deployment.md)
