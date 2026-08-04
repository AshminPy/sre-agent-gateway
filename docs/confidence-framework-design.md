# Confidence Framework — Design (schema + scoring policy)

> Step 1 deliverable of the confidence-redesign work (PRODUCTION-LAUNCH-PLAN.md priority 2).
> Written before implementation, per the required process. Grounded in the actual current
> code (`agent/nodes/task_evaluator.py`, `agent/nodes/rca_builder.py`, `agent/main.py`) and
> every downstream consumer found during dependency mapping — not designed in a vacuum.

## Why two scores, not one

Today: one `confidence` float, self-assigned by the LLM, then capped by
`min(1, evidence_count/4) + 0.25` (`task_evaluator.py`). This conflates two different
questions and treats four log lines from the same container as four independent facts.

New: two deterministic scores, both **computed by application code, never by the LLM**.

## Downstream compatibility constraint (from dependency mapping)

`confidence_band` (values `auto`/`review`/`escalate`) is filtered directly by 3 Terraform
log-based metrics and 2 alert policies (`iac/agent/monitoring.tf`,
`jsonPayload.confidence_band="escalate"` etc.) — this field name and its 3-value vocabulary
**must be preserved** in the stdout `obs_event` JSON. `run_eval.py`/`golden_cases.py` read
`summary["likely_root_cause"]`, `summary["confidence_score"]`, `summary["confidence_band"]` —
these field names on `final_summary` must keep working. See "Backward compatibility" below.

---

## 1. Evidence domains

```python
class EvidenceDomain(str, Enum):
    KUBERNETES_STATUS   = "kubernetes_status"    # pod phase, restart count, termination reason
    KUBERNETES_EVENTS   = "kubernetes_events"
    CURRENT_LOGS        = "current_logs"
    PREVIOUS_LOGS       = "previous_logs"
    WORKLOAD_CONFIG     = "workload_config"       # deployment/replicaset spec
    RESOURCE_LIMITS     = "resource_limits"        # requests/limits
    CLOUD_LOGGING       = "cloud_logging"
    METRICS             = "metrics"
    CHANGE_HISTORY      = "change_history"         # deployment/rollout history
    ALERT_METADATA      = "alert_metadata"         # PagerDuty payload fields
    RUNBOOK_HISTORY     = "runbook_history"        # memory bank / past RCAs
    UNKNOWN             = "unknown"                # unclassified — always logged, never silently dropped
```

Classification is by **tool name** (deterministic mapping table, e.g. `get_current_logs` →
`CURRENT_LOGS`, `list_events` → `KUBERNETES_EVENTS`), not by content inspection — cheap,
reliable, and extensible: adding a new MCP tool just adds one table entry, no scorer changes.
An unmapped tool falls into `UNKNOWN`, which is logged as a gap (per your "future MCP sources"
requirement) rather than silently miscounted as a new independent domain.

**Two evidence items in the same domain count as ONE independent source** for corroboration
purposes (directly implements "ten log lines from one container are usually one evidence
source"). `CURRENT_LOGS` and `PREVIOUS_LOGS` are treated as *related*, not fully independent —
weighted at 0.5 of a full independent source when both are present together.

## 2. Claim objects

```python
class ClaimType(str, Enum):
    OBSERVED_FACT       = "observed_fact"       # directly stated by evidence, no inference
    SUPPORTED_INFERENCE = "supported_inference" # reasonable conclusion from observed facts
    HYPOTHESIS          = "hypothesis"           # plausible but not confirmed
    UNKNOWN             = "unknown"
    RECOMMENDATION      = "recommendation"       # not a factual claim, no confidence impact

@dataclass
class Claim:
    claim_id:               str   # "claim_001"
    text:                    str
    claim_type:              ClaimType
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]
    support_strength:        float  # 0-1, deterministic — see scorer.py
    grounding_status:        str    # "grounded" | "phantom_evidence" | "no_overlap" | "ungrounded"
```

The root cause is **never** rendered as `OBSERVED_FACT` unless every supporting claim is
`OBSERVED_FACT` — inference and hypothesis stay labeled as such through to the final RCA text.

## 3. Contradictions

```python
@dataclass
class Contradiction:
    contradiction_id: str
    claim_id:          str
    description:        str
    evidence_id_a:       str
    evidence_id_b:       str
    kind:                str   # "wrong_resource" | "wrong_time_window" | "semantic" (LLM-flagged)
    severity:            float # 0-1, feeds root_cause_confidence penalty
```

Two detection paths:
- **Deterministic (code-only):** evidence tagged with a different pod/namespace/cluster than
  `resolved_context`, or a timestamp outside the incident window — both checked directly
  against structured evidence fields, no LLM involved.
- **LLM-flagged, app-enforced:** the RCA builder prompt now asks the model to self-report any
  evidence that contradicts its own proposed cause (`contradicting_evidence_ids` per claim).
  The model flags candidates; **the app decides the score penalty**, never the model — this is
  the "app must calculate the final score" requirement applied to contradictions specifically.
  **Known limitation (documented, not silently skipped):** this is a single-pass self-report,
  not an adversarial second-pass skeptic check. A dedicated adversarial contradiction pass is a
  real future improvement, not built in this iteration — flagged in the final report.

## 4. Hypotheses

```python
@dataclass
class Hypothesis:
    hypothesis_id:       str
    description:           str
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]
    missing_evidence:      list[str]
    status:                str  # "active" | "weakened" | "eliminated" | "selected"
```

The RCA builder prompt now asks the model for `alternative_hypotheses_considered` alongside
the primary cause. An unresolved `active` alternative with real supporting evidence applies the
`alternative_hypothesis_penalty` from policy.

---

## 5. Investigation Completeness — 100% deterministic, no LLM input

```
investigation_completeness.score = weighted average of:
  routing_confirmed        (1.0 if resolved_context has cluster+namespace+pod/workload, else 0.0)
  identity_confirmed        (1.0 if MCP source selection was deterministic, not defaulted)
  required_evidence_coverage (domains collected / domains required for this incident_type,
                               per policy.evidence_requirements — NOT raw evidence count)
  freshness                 (1.0 if evidence timestamps within max_age_seconds, decays linearly after)
  tool_success               ((tool_calls - failed_calls) / tool_calls, 1.0 if zero calls attempted... 
                               forced to 0 if zero calls — see edge case below)
  iteration_budget           (1.0 unless max_steps was hit before required domains were covered)
```

Computed entirely from `AgentState` (evidence_store domains, tool_history, resolved_context,
investigation.current_step/max_steps) — the LLM is not consulted for this score at all. This
directly satisfies "do not calculate this from evidence count alone" and removes the LLM from
a place it was never suited to be deterministic in.

## 6. Root-Cause Confidence — deterministic given LLM-proposed claims

```
root_cause_confidence.score = base_score − penalties, base_score from:
  direct_support             (fraction of root-cause claims that are OBSERVED_FACT vs inference)
  independent_corroboration  (count of distinct evidence DOMAINS supporting the claim, capped,
                               using the domain-weighting from §1 — not raw evidence count)
  resource_identity_match     (1.0 if all supporting evidence matches resolved_context exactly)
  time_correlation             (1.0 if evidence timestamps correlate with incident window)
  claim_grounding               (from claim.grounding_status — phantom/no-overlap claims score 0)
minus:
  contradiction_penalty         (policy-configured, scales with contradiction severity + count)
  alternative_hypothesis_penalty (policy-configured, applies when a credible alternative is unresolved)
  missing_evidence_penalty       (policy-configured, scales with required-but-absent evidence domains)
```

## 7. Outcome states (replaces "always produce a confident RCA")

```python
class InvestigationOutcome(str, Enum):
    CONFIRMED             = "confirmed"
    PROBABLE              = "probable"
    POSSIBLE               = "possible"
    INSUFFICIENT_EVIDENCE  = "insufficient_evidence"
    UNKNOWN                 = "unknown"
    CONFLICTING_EVIDENCE     = "conflicting_evidence"
```

Outcome is derived deterministically from both scores + gate checks (§8), not asked of the LLM.

## 8. Confidence bands + publication gates (legacy-compatible)

`confidence_band` (`auto`/`review`/`escalate`) is **kept as the legacy field**, computed from
the new scores via a policy-defined mapping, so the 3 existing log metrics + 2 alert policies
keep working unmodified. `auto` additionally requires **all** of: no unresolved material
contradiction, claim grounding valid (existing `_validate_citations` gate, extended not
replaced), required-identity checks passed, schema validation passed. A numeric threshold alone
can never produce `auto` — matching "do not auto-post broadly based only on a numeric
threshold."

## 9. Schema (API response `summary` object — additions are additive)

```json
{
  "schema_version": "2.0",
  "likely_root_cause": "<unchanged — now derived from the root-cause claim, single source>",
  "confidence_score": 0.74,
  "confidence_deprecated": true,
  "confidence_band": "review",
  "outcome": "probable",
  "investigation_completeness": {
    "score": 0.82, "band": "complete_with_gaps",
    "components": { "routing_confirmed": 1.0, "identity_confirmed": 1.0,
                     "required_evidence_coverage": 0.75, "freshness": 1.0,
                     "tool_success": 0.8, "iteration_budget": 1.0 },
    "gaps": ["Previous container logs were unavailable"]
  },
  "root_cause_confidence": {
    "score": 0.74, "band": "review_required",
    "components": { "direct_support": 0.8, "independent_corroboration": 0.7,
                     "resource_identity_match": 1.0, "time_correlation": 0.9,
                     "claim_grounding": 0.8, "contradiction_penalty": 0.0,
                     "alternative_hypothesis_penalty": 0.2, "missing_evidence_penalty": 0.1 },
    "reasons": ["The termination reason supports OOMKilled",
                "The memory limit corroborates the finding",
                "Node-level memory pressure was not checked"]
  },
  "claims": [ { "claim_id": "claim_001", "text": "...", "claim_type": "observed_fact",
                "supporting_evidence_ids": ["ev_001"], "contradicting_evidence_ids": [],
                "support_strength": 0.9, "grounding_status": "grounded" } ],
  "hypotheses": [ { "hypothesis_id": "hyp_001", "description": "...", "status": "eliminated",
                     "supporting_evidence_ids": [], "contradicting_evidence_ids": ["ev_002"],
                     "missing_evidence": [] } ],
  "contradictions": [],
  "policy_version": "1.0.0",

  "evidence_gaps": ["<unchanged>"],
  "evidence_chain": ["<unchanged>"],
  "requires_human_review": true,
  "sources_skipped": [],
  "sre_feedback": null, "sre_notes": null, "validation_status": "pending",
  "gcs_enriched": false, "tools_called": [], "failed_tools": [],
  "investigation_context": {}
}
```

## 10. Policy configuration (versioned, validated at startup)

`agent/confidence/policy.py` — a single `ConfidencePolicy` dataclass, `POLICY_VERSION = "1.0.0"`,
loaded once at graph-compile time, `validate()` raises at startup if weights don't sum
correctly or a required field is absent (never a silent misconfiguration at runtime).

```python
@dataclass(frozen=True)
class ConfidencePolicy:
    version: str = "1.0.0"
    completeness_weights: dict   # routing_confirmed, identity_confirmed, required_evidence_coverage,
                                  # freshness, tool_success, iteration_budget — sum to 1.0
    confidence_weights: dict     # direct_support, independent_corroboration, resource_identity_match,
                                  # time_correlation, claim_grounding — sum to 1.0 (before penalties)
    contradiction_penalty_per_item: float = 0.15
    contradiction_penalty_cap: float = 0.6
    alternative_hypothesis_penalty: float = 0.15
    missing_evidence_penalty_per_domain: float = 0.10
    evidence_max_age_seconds: int = 900
    evidence_requirements: dict[str, EvidenceRequirement]  # per incident_type, see below
    band_thresholds: dict        # {"auto": 0.85, "review": 0.65} — same numbers as today,
                                  # now explicitly configurable instead of hardcoded in task_evaluator.py
    max_score_missing_critical_evidence: float = 0.65  # hard cap, independent of the weighted score
    max_score_unresolved_contradiction: float = 0.65
    max_score_unresolved_hypothesis: float = 0.75

@dataclass(frozen=True)
class EvidenceRequirement:
    required_now: list[EvidenceDomain]
    optional_corroborating: list[EvidenceDomain]
    future_source: list[EvidenceDomain]   # documented as not yet available — never penalized
```

Initial per-incident-type requirements (OOMKilled, ImagePullBackOff, CrashLoopBackOff) match
your spec exactly — `metrics` and `change_history` are marked `future_source` since no MCP tool
currently provides them, so their absence never makes an investigation look artificially
incomplete.

**Explicitly not claimed calibrated.** All weights/penalties above are an initial policy
carried over from the current hardcoded values where a direct equivalent exists (e.g. band
thresholds 0.85/0.65 unchanged) and reasoned defaults elsewhere. Per your item 16, this is
marked `policy_version: "1.0.0-uncalibrated"` in code comments and must be validated against
the golden dataset before being trusted for real launch decisions.

## 11. Backward compatibility plan

| Consumer | Field(s) read | Plan |
|---|---|---|
| `iac/agent/monitoring.tf` (3 metrics + 2 alerts) | `jsonPayload.confidence_band` (stdout obs_event) | Unchanged field name + `auto/review/escalate` vocabulary, now computed from new scores via policy mapping |
| `agent/eval/run_eval.py` | `summary["likely_root_cause"]`, `["confidence_score"]`, `["confidence_band"]` | All three preserved; `confidence_score` now maps to `root_cause_confidence.score` (documented explicitly, not left ambiguous per your instruction) |
| `agent/eval/golden_cases.py` | `expected_confidence_min` thresholds (0.5–0.65) | Preserved as-is; will likely need recalibration once real runs are compared (step 11-12) — expected, not a break |
| `scripts/smoke_test.sh` | `"status":"done"` OR `rca_report` text | No change needed — check is already loose/robust |
| `main.py._mb_store` (Memory Bank) | `confidence` (float) | **New gate added** (§12) — was previously ungated, a real pre-existing gap, not a preserved behavior |
| `invoke_agent.py` | prints raw JSON, no assertions | No risk |

## 12. Memory Bank write gate (new — closes a real gap found during mapping)

`main.py:853` currently calls `_mb_store()` for any `status not in (failed, blocked)` —
**no confidence or validation gate exists today**, despite the function's own docstring
claiming RCA-poisoning prevention (it only dedups by pod+incident_type). New gate: only write
to Memory Bank when `outcome in (CONFIRMED, PROBABLE)` **and** `confidence_band == "auto"` —
matching your item 11 requirement literally, since human validation infrastructure
(`sre_feedback`/`validation_status`) exists as fields but nothing currently consumes it to gate
memory writes either; flagged as a further improvement, not built here (real scope boundary —
building a human-review-approval pipeline is a separate, larger effort).

---

## Legend for the rest of the implementation

Everything above is implemented as pure, isolated functions in `agent/confidence/`, imported
by `task_evaluator.py` and `rca_builder.py` — neither node's control flow changes structurally,
only what feeds the score and what the score means.
