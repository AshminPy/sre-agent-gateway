## Purpose

Defines the 9 management-agreed acceptance areas a Phase 1 release of sre-agent-gateway must satisfy, so a release decision can be made against a written bar instead of memory.

## ADDED Requirements

### Requirement: Connect Gateway for on-prem clusters
The system SHALL support investigating a non-GKE / on-prem Kubernetes cluster through Connect Gateway in production, not only in a local manual test.

#### Scenario: On-prem cluster investigation runs end-to-end in production
- **WHEN** an incident is reported against a registered non-GKE cluster
- **THEN** the agent authenticates via Connect Gateway, runs read-only tools against that cluster, and returns an RCA — with no manual, out-of-band step required

### Requirement: Plug-and-play cluster onboarding
The system SHALL let a new cluster (GKE or non-GKE) be onboarded via configuration/Terraform variables only, with no per-cluster code change.

#### Scenario: New GKE cluster, same project
- **WHEN** an operator adds a cluster entry to the Terraform cluster variable and applies
- **THEN** the agent can route to and investigate that cluster with no Python code change

#### Scenario: New GKE cluster, different project
- **WHEN** an operator onboards a GKE cluster that lives in a different GCP project than the default
- **THEN** the required cross-project IAM is granted by configuration alone, with no manual IAM edit

#### Scenario: New non-GKE cluster
- **WHEN** an operator onboards a non-GKE / on-prem cluster
- **THEN** the same configuration-only onboarding path applies, backed by a real (not fixture-only) registration mechanism

### Requirement: Config-only LLM switching
The system SHALL allow switching the underlying LLM provider/model via configuration, without code changes to the agent's core logic.

#### Scenario: Operator switches provider
- **WHEN** an operator changes the configured LLM provider (e.g. from Gemini to another supported provider)
- **THEN** the agent runs correctly against the new provider with no changes to `agent/main.py`'s core logic, using the provider-agnostic LLM abstraction

### Requirement: Full read-only custom MCP tool parity
The custom Cloud Run MCP SHALL expose read-only tool coverage equivalent to the GKE Remote MCP path for all Phase 1 incident scenario classes.

#### Scenario: Config/secret-error scenario class
- **WHEN** an incident involves a missing/misconfigured ConfigMap, Secret, or volume mount
- **THEN** the custom MCP's pod-detail tool returns volume and volumeMount information sufficient to diagnose it, matching GKE Remote MCP coverage

### Requirement: Accurate cluster and MCP routing with no silent fallback
The system SHALL route each request to the correct cluster and correct MCP source deterministically, and SHALL fail closed (not silently fall back) when routing cannot be resolved.

#### Scenario: Ambiguous or unresolvable routing
- **WHEN** the requested cluster or MCP source cannot be deterministically resolved
- **THEN** the system stops and reports the failure rather than silently defaulting to another cluster or MCP source

#### Scenario: Non-GKE path proven live
- **WHEN** routing logic is exercised against a real non-GKE cluster (not only unit tests)
- **THEN** the same deterministic routing and safe-stop behavior is observed live

### Requirement: Agent Gateway enforcement with no bypass
All agent and tool traffic (LLM calls, GKE Remote MCP calls, custom Cloud Run MCP calls) SHALL transit Agent Gateway. Any traffic that does not SHALL be explicitly identified.

#### Scenario: Custom MCP traffic path
- **WHEN** the agent calls the custom Cloud Run MCP
- **THEN** that call is authorized via Agent Gateway's IAP `REQUEST_AUTHZ` extension, not a direct unmediated Cloud Run invocation

#### Scenario: Bypass identification
- **WHEN** any call path exists that does not transit Agent Gateway
- **THEN** it is named explicitly, with the specific traffic type and reason, rather than left undocumented

### Requirement: Full observability field set
The system SHALL emit a defined, complete set of observability fields for every investigation run, and SHALL degrade telemetry (not the investigation result) on telemetry failure.

#### Scenario: Telemetry failure during a successful investigation
- **WHEN** the observability pipeline fails partway through emitting `obs_event`
- **THEN** the investigation result returned to the user is unaffected, and the event is degraded to a minimal event rather than dropped or treated as an investigation failure

### Requirement: Accurate, evidence-backed RCA
Every RCA produced by the agent SHALL be backed by the actual evidence collected (logs, events, tool outputs) during that run, not by unsupported inference.

#### Scenario: RCA cites real evidence
- **WHEN** the agent produces a root-cause conclusion
- **THEN** the conclusion is traceable to specific tool outputs/evidence captured in that run's `evidence_count`/evidence storage, not fabricated

### Requirement: Model Armor / security validation
The system SHALL demonstrate that Model Armor (or an equivalent app-level control) blocks a real malicious payload without materially degrading legitimate RCA quality, for both the GKE Remote MCP path and the custom MCP path.

#### Scenario: Positive block test
- **WHEN** a known-malicious prompt-injection or jailbreak payload is sent through the agent
- **THEN** Model Armor (floor setting or app-level) detects and blocks it, with the block evidenced in Cloud Logging

#### Scenario: No unacceptable false-positive cost
- **WHEN** legitimate incident-investigation traffic is run through the same enabled control
- **THEN** truthful RCA is not blocked or degraded at an unacceptable rate

#### Scenario: Custom MCP path covered (issue #203)
- **WHEN** traffic routes through the custom MCP as the primary path
- **THEN** that path has the same Model Armor coverage as the GKE Remote MCP path, or the gap is explicitly named as unresolved
