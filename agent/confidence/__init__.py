"""agent.confidence — deterministic, evidence-backed investigation scoring.

Two axes, computed by application code, never by the LLM:
  - investigation_completeness: did we collect what this incident type needs?
  - root_cause_confidence: does the evidence actually support the proposed cause?

See docs/confidence-framework-design.md for the full design and rationale.
"""
from agent.confidence.policy import POLICY, ConfidencePolicy
from agent.confidence.evidence_domains import EvidenceDomain, classify_tool
from agent.confidence.models import (
    Claim, ClaimType, Contradiction, Hypothesis, InvestigationOutcome,
)
from agent.confidence.scorer import (
    score_investigation_completeness, score_root_cause_confidence, derive_outcome,
)

__all__ = [
    "POLICY", "ConfidencePolicy",
    "EvidenceDomain", "classify_tool",
    "Claim", "ClaimType", "Contradiction", "Hypothesis", "InvestigationOutcome",
    "score_investigation_completeness", "score_root_cause_confidence", "derive_outcome",
]
