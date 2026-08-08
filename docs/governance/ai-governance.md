# AI Governance

> **Implementation Status:** Reference page — see linked pages for status of each mechanism referenced.
> **Last Verified:** 2026-08-08
> **Owner:** SRE Agent platform team.

Answers the questions Security, Risk, and Audit are most likely to ask.

## Transparency

**Can we see why the agent reached a conclusion?** Yes — every RCA includes the full evidence chain, the claims/hypotheses/contradictions the confidence scorer evaluated, and the component-level breakdown of both completeness and root-cause-confidence scores (see [Confidence Scoring](../architecture/confidence.md)). This is not a black-box output.

**Can every RCA claim be traced back to evidence?** Root claims are checked against real evidence IDs by deterministic code (phantom-citation detection + keyword-overlap check) — a claim citing evidence that doesn't exist is caught, not silently trusted. See [Confidence Scoring](../architecture/confidence.md#claims-hypotheses-and-contradictions).

**Can we see every tool the agent called?** Yes — `tool_history` in every investigation, surfaced in both the RCA and the structured logs (`sre-agent-investigations`), including failures. See [Logs](../operations/logging.md).

## Human control

**Can the agent make production changes?** No — confirmed at four independent layers (see [Security Operations](security.md#kubernetes-access-is-read-only)). It cannot delete, create, patch, exec, scale, or otherwise mutate anything in Kubernetes.

**Who approves remediation?** A human. The agent's `suggested_remediation` field is text in the RCA, never an executed action.

**How do we prevent autonomous actions?** By construction — there is no code path in this agent that calls a mutating Kubernetes API. This isn't a policy the agent chooses to follow; the capability doesn't exist in its tool set.

## Reliability

**How do we know whether the RCA is correct?** Today: golden-case evaluation (14 cases, deterministic scoring — see [Evaluation](../architecture/evaluation.md)) plus, in production, the confidence-scoring gates. There is **no automated pipeline turning real human-reviewed RCA feedback into new tests** yet — that loop is planned but not built.

**What does confidence actually mean?** A deterministic score computed by application code from code-checkable facts (evidence coverage, grounding, contradictions) — not the model self-reporting how sure it feels. See [Confidence Scoring](../architecture/confidence.md). Note the scoring **policy itself is explicitly labeled uncalibrated** (`POLICY_VERSION = "1.0.0-uncalibrated"`) — the mechanism is real, the specific numbers haven't been validated against real incident outcomes yet.

**What happens when confidence is low?** `requires_human_review=True`, `confidence_band` reflects it (`review`/`escalate`), and the RCA is not written to persistent long-term memory (see [Memory](../architecture/memory.md)).

**What happens when evidence is missing?** The agent explicitly says so — it never fabricates a root cause with zero evidence; a hard code override forces an honest "no evidence extracted" statement in that case.

## Accountability

**Who owns agent output?** The SRE Agent platform team owns the system; the reviewing SRE (human) owns the decision to act on any given RCA.

**Who validates memory?** No one, formally, today — this is a documented gap (see [Memory](../architecture/memory.md)). The `sre_feedback`/`validation_status` fields exist specifically to eventually support this, but the review workflow isn't built.

**Who approves new tools / new clusters?** Per this document's recommended process — see [Change Management](change-management.md).

## Data governance

**What information is sent to the LLM?** A curated, per-node slice of state — never raw tool output, never other investigations' data. See [Context and State](../architecture/context-and-state.md) for the full breakdown of what is and isn't passed.

**Where is evidence stored?** GCS, redacted before write (emails, IPs, bearer tokens, secret-shaped fields). See [Evidence Architecture](../architecture/evidence-architecture.md).

**How long is data retained?** Evidence: 90 days. Full RCAs: 365 days. Memory Bank: retention not explicitly configured in this repo's Terraform — **UNKNOWN**, verify against the live resource.

**Can incidents leak between sessions?** No — each investigation is a fresh, isolated `AgentState`; nothing carries over except the deliberate, gated Memory Bank recall. See [Context and State](../architecture/context-and-state.md#how-we-prevent-one-investigation-from-contaminating-another).

**How is sensitive information handled?** Redaction at evidence-extraction time (before storage or model exposure); Model Armor was *intended* as an additional content-safety layer but is **currently not active in the live configuration** — see [Security Operations](security.md#️-model-armor--the-most-significant-governance-finding-in-this-review). This should be weighed explicitly if this system will handle more sensitive incident data than it does today.

## Model governance

**Which model are we using?** Live: `gemini-2.5-pro` (set via Terraform, `iac/agent/terraform.tfvars`). Code default (if the env var were unset): `gemini-2.5-flash`.

**What happens if Google changes/deprecates the model?** No automated detection or fallback exists — a model deprecation would need to be caught manually (Google's own deprecation notices) and addressed via a Terraform variable change + redeploy + re-run of the golden eval suite before trusting the new model version.

**How do we test model upgrades?** Manually today — run the golden eval suite (`--mode remote`) against a candidate deployment before promoting it. Not automated as a gate (see [Evaluation](../architecture/evaluation.md)).

**Can we swap models?** Yes, in principle — `GEMINI_MODEL` is a Terraform variable, and `agent/gemini_client.py`'s pricing constants and `thinking_budget` logic are model-family-aware. Note the code's design intent ("thinking disabled for deterministic responses") does not currently hold in practice for `gemini-2.5-pro`, which requires `thinking_budget > 0` — a live, known discrepancy worth understanding before assuming model-swap is a zero-risk config change.

**Are we locked into Google?** The core reasoning is Gemini-specific (`agent/gemini_client.py`), and the deployment platform (Agent Engine, Agent Gateway, Agent Identity) is GCP-specific by design — this is not a portable-by-default architecture.

---

**Related pages:** [Security Operations](security.md) · [Confidence Scoring](../architecture/confidence.md) · [Executive FAQ](../management/executive-faq.md)
