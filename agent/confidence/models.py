"""Data structures for claim grounding, contradictions, and competing hypotheses.

Plain dataclasses, not pydantic — this package is imported by the LangGraph node hot path and
has zero need for the validation/serialization machinery a request-boundary model would need.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ClaimType(str, Enum):
    OBSERVED_FACT = "observed_fact"
    SUPPORTED_INFERENCE = "supported_inference"
    HYPOTHESIS = "hypothesis"
    UNKNOWN = "unknown"
    RECOMMENDATION = "recommendation"


class InvestigationOutcome(str, Enum):
    CONFIRMED = "confirmed"
    PROBABLE = "probable"
    POSSIBLE = "possible"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNKNOWN = "unknown"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


@dataclass
class Claim:
    claim_id: str
    text: str
    claim_type: ClaimType
    supporting_evidence_ids: list = field(default_factory=list)
    contradicting_evidence_ids: list = field(default_factory=list)
    support_strength: float = 0.0
    # grounded | weak_overlap | phantom_evidence | no_overlap | ungrounded
    # | failed_evidence_only | empty_evidence | empty_claim   (added 2026-08-27)
    # Defaults fail CLOSED (0.0 / "ungrounded") — a Claim that is never grounded
    # contributes nothing, rather than starting from credit it has not earned.
    grounding_status: str = "ungrounded"

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "claim_type": self.claim_type.value,
            "supporting_evidence_ids": self.supporting_evidence_ids,
            "contradicting_evidence_ids": self.contradicting_evidence_ids,
            "support_strength": round(self.support_strength, 3),
            "grounding_status": self.grounding_status,
        }


@dataclass
class Contradiction:
    contradiction_id: str
    claim_id: str
    description: str
    evidence_id_a: str
    evidence_id_b: str
    kind: str  # wrong_resource | wrong_time_window | semantic
    severity: float = 0.5

    def to_dict(self) -> dict:
        return {
            "contradiction_id": self.contradiction_id,
            "claim_id": self.claim_id,
            "description": self.description,
            "evidence_id_a": self.evidence_id_a,
            "evidence_id_b": self.evidence_id_b,
            "kind": self.kind,
            "severity": round(self.severity, 3),
        }


@dataclass
class Hypothesis:
    hypothesis_id: str
    description: str
    supporting_evidence_ids: list = field(default_factory=list)
    contradicting_evidence_ids: list = field(default_factory=list)
    missing_evidence: list = field(default_factory=list)
    status: str = "active"  # active | weakened | eliminated | selected

    def to_dict(self) -> dict:
        return {
            "hypothesis_id": self.hypothesis_id,
            "description": self.description,
            "supporting_evidence_ids": self.supporting_evidence_ids,
            "contradicting_evidence_ids": self.contradicting_evidence_ids,
            "missing_evidence": self.missing_evidence,
            "status": self.status,
        }
