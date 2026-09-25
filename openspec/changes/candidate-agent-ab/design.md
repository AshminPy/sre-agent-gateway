## Context
A/B test of a candidate agent build against the current agent in the personal environment.

## Architecture-quality answers
- Likely to change: which source is the candidate -> build-time variable, not code.
- Another candidate/environment: same slot, new archive; slot is count-gated.
- Automation: `make package-candidate` + `terraform apply`; RBAC subject added via `k8s/rbac.yaml`.
- Dependency failure: candidate failure cannot affect the primary engine (separate resource, separate identity, separate memory bank).
- Observability: same logs/traces/metrics; runs distinguished by engine ID / run ID. Log-based metrics aggregate both engines while the candidate is enabled (accepted, temporary).
- Testing: `terraform validate`, existing `terraform test` suite, `terraform plan` (no change to existing resources), then live eval against both engine IDs.
- Deploy/rollback: flag + apply; tag `pre-candidate-agent-2026-09-24`.
- Security boundary: candidate identity gets exactly the primary's runtime role list, no broader. Cross-project GKE IAM already uses the project principalSet; namespace RBAC needs the candidate principal added explicitly.
- Scale/cost: min_instances 0, max_instances 10; second memory bank engine.

## DECISION
DECISION            Duplicate candidate resources (count-gated) instead of refactoring existing resources to for_each.
EVIDENCE            iac/agent/iam.tf and agent_engine.tf address the primary engine directly; tests in iac/agent/tests reference google_vertex_ai_reasoning_engine.sre_agent.
WHY                 Refactoring would move state addresses of live IAM and engine resources; a mistake recreates the primary agent. Duplication keeps the primary plan empty.
TRADEOFFS           Some duplicated HCL while the candidate slot exists.
VALIDATION METHOD   terraform plan must show 0 to change / 0 to destroy for existing resources.
UNCERTAINTY         Candidate runtime behaviour against this environment's MCP -> RUNTIME VALIDATION REQUIRED.
