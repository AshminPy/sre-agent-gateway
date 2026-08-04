"""Versioned, validated confidence scoring policy.

No weight, threshold, or penalty lives inline in task_evaluator.py or rca_builder.py — every
number that affects a score lives here, in one place, with a version stamped into every
investigation result for audit and future calibration.

POLICY_VERSION is explicitly "1.0.0-uncalibrated": these are reasoned initial defaults (band
thresholds carried over unchanged from the current hardcoded 0.85/0.65; other weights set from
first principles), not statistically calibrated against real incident outcomes. Calibration
against the golden dataset (agent/eval/golden_cases.py + real SRE-validated RCAs) is required
before these numbers should be trusted for a real launch decision — see
docs/confidence-framework-design.md §10 and the final verification report.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent.confidence.evidence_domains import EvidenceDomain

POLICY_VERSION = "1.0.0-uncalibrated"


@dataclass(frozen=True)
class EvidenceRequirement:
    required_now: tuple = ()
    optional_corroborating: tuple = ()
    future_source: tuple = ()  # not yet available from any MCP tool — never penalized


@dataclass(frozen=True)
class ConfidencePolicy:
    version: str = POLICY_VERSION

    # Investigation Completeness weights — must sum to 1.0
    completeness_weights: dict = field(default_factory=lambda: {
        "routing_confirmed": 0.15,
        "identity_confirmed": 0.10,
        "required_evidence_coverage": 0.40,
        "freshness": 0.10,
        "tool_success": 0.15,
        "iteration_budget": 0.10,
    })

    # Root-Cause Confidence weights (pre-penalty) — must sum to 1.0
    confidence_weights: dict = field(default_factory=lambda: {
        "direct_support": 0.30,
        "independent_corroboration": 0.25,
        "resource_identity_match": 0.20,
        "time_correlation": 0.15,
        "claim_grounding": 0.10,
    })

    contradiction_penalty_per_item: float = 0.15
    contradiction_penalty_cap: float = 0.60
    alternative_hypothesis_penalty: float = 0.15
    missing_evidence_penalty_per_domain: float = 0.10
    missing_evidence_penalty_cap: float = 0.40

    evidence_max_age_seconds: int = 900

    # Same numeric thresholds as today's hardcoded task_evaluator.py bands — unchanged on
    # purpose, so this migration doesn't itself shift auto/review/escalate behavior; only the
    # *inputs* to the score change.
    band_thresholds: dict = field(default_factory=lambda: {"auto": 0.85, "review": 0.65})

    # Hard caps — independent of the weighted score, applied after it. A missing-critical-
    # evidence or unresolved-contradiction case can never reach "auto" purely by having a high
    # weighted average elsewhere.
    max_score_missing_critical_evidence: float = 0.65
    max_score_unresolved_contradiction: float = 0.65
    max_score_unresolved_hypothesis: float = 0.75

    evidence_requirements: dict = field(default_factory=lambda: {
        "OOMKilled": EvidenceRequirement(
            required_now=(
                EvidenceDomain.KUBERNETES_STATUS,
                EvidenceDomain.KUBERNETES_EVENTS,
                EvidenceDomain.PREVIOUS_LOGS,
            ),
            optional_corroborating=(EvidenceDomain.WORKLOAD_CONFIG,),
            future_source=(EvidenceDomain.METRICS,),
        ),
        "ImagePullBackOff": EvidenceRequirement(
            required_now=(
                EvidenceDomain.KUBERNETES_STATUS,
                EvidenceDomain.KUBERNETES_EVENTS,
            ),
            optional_corroborating=(EvidenceDomain.WORKLOAD_CONFIG,),
            future_source=(EvidenceDomain.CLOUD_LOGGING,),
        ),
        "CrashLoopBackOff": EvidenceRequirement(
            required_now=(
                EvidenceDomain.CURRENT_LOGS,
                EvidenceDomain.KUBERNETES_STATUS,
                EvidenceDomain.KUBERNETES_EVENTS,
            ),
            optional_corroborating=(
                EvidenceDomain.PREVIOUS_LOGS,
                EvidenceDomain.WORKLOAD_CONFIG,
            ),
            future_source=(EvidenceDomain.CHANGE_HISTORY,),
        ),
        # Fallback for any incident_type not explicitly listed — deliberately light so an
        # unknown incident type doesn't get unfairly penalized for missing domain-specific
        # evidence the policy never asked for.
        "_default": EvidenceRequirement(
            required_now=(EvidenceDomain.KUBERNETES_STATUS,),
            optional_corroborating=(
                EvidenceDomain.KUBERNETES_EVENTS,
                EvidenceDomain.CURRENT_LOGS,
            ),
            future_source=(),
        ),
    })

    def evidence_requirement_for(self, incident_type: str) -> EvidenceRequirement:
        return self.evidence_requirements.get(incident_type, self.evidence_requirements["_default"])

    def validate(self) -> None:
        """Raise at startup, never at request time, if the policy is misconfigured."""
        for name, weights in (
            ("completeness_weights", self.completeness_weights),
            ("confidence_weights", self.confidence_weights),
        ):
            total = sum(weights.values())
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"ConfidencePolicy.{name} must sum to 1.0, got {total}")
        if not (0.0 <= self.band_thresholds["review"] < self.band_thresholds["auto"] <= 1.0):
            raise ValueError(
                f"ConfidencePolicy.band_thresholds invalid: {self.band_thresholds}"
            )
        for cap_name in (
            "max_score_missing_critical_evidence",
            "max_score_unresolved_contradiction",
            "max_score_unresolved_hypothesis",
        ):
            v = getattr(self, cap_name)
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"ConfidencePolicy.{cap_name} must be in [0,1], got {v}")


POLICY = ConfidencePolicy()
POLICY.validate()
