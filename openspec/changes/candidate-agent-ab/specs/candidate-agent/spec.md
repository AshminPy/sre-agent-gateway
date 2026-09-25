## ADDED Requirements

### Requirement: Optional candidate agent for side-by-side evaluation
The agent stack SHALL support deploying a second, independent agent engine that shares the
environment of the primary agent without changing any primary-agent resource.

#### Scenario: Disabled by default
- **WHEN** `enable_candidate_agent` is unset
- **THEN** no candidate resources are planned and no candidate archive is required

#### Scenario: Enabled without touching the primary agent
- **WHEN** `enable_candidate_agent = true` and `agent-candidate.tar.gz` exists
- **THEN** the plan only adds candidate resources (engine, memory bank, identity grants)
- **AND** no existing resource is updated, replaced or destroyed

#### Scenario: Isolated memory
- **WHEN** the candidate engine runs an investigation
- **THEN** it reads and writes its own memory bank, never the primary agent's
