# ADR-002: Agent Identity and Agent Gateway binding

Status: Accepted

## Context

The Agent Gateway (egress governance) requires the reasoning engine to run with
`identity_type = AGENT_IDENTITY` — a per-agent SPIFFE identity — rather than a
service account. Two platform facts shape the design:

1. The reasoning engine's `agent_gateway_config` (which attaches an engine to a
   gateway) is **not yet exposed by the Terraform Google provider**. The gateway
   resource, its network attachment, firewall, and authz policies *are*.
2. `agent_gateway_config` and `pscInterfaceConfig` are **mutually exclusive** on
   the engine — the API rejects both. Only the gateway needs a network
   attachment; the engine must not set one.

## Decision

- Deploy the engine with `identity_type = AGENT_IDENTITY` and **no**
  `service_account` and **no** `pscInterfaceConfig`.
- Grant every runtime role to the derived Agent Identity principal (never a
  service account).
- Build the gateway (and its PSC-Interface network attachment, `allow-psc-i`
  firewall, IAP `REQUEST_AUTHZ` and Model Armor `CONTENT_AUTHZ` authz chains) in
  Terraform.
- **Attach the engine to the gateway with a post-apply script**
  (`scripts/attach_gateway_to_engine.sh`, a REST PATCH) — the one step Terraform
  cannot yet express. Idempotent and a no-op when the gateway is disabled.
- Keep the gateway a feature flag (`enable_agent_gateway`, default true) so a
  gateway-free deploy is a first-class, fully-working path.

## Operational note: asynchronous data-plane provisioning

A newly-created Agent Gateway's data plane provisions **asynchronously on
Google's side** and may take a while (observed: up to a few hours) before it
serves traffic — during which egress calls can fail silently. This is expected
platform behaviour, not a misconfiguration. If the agent's calls fail right
after enabling the gateway, wait and retry before debugging config. A
project-level provisioning issue (e.g. an older, pre-GA project) can prevent it
entirely — in that case the identical setup works in a fresh project, which
points to the platform side rather than this configuration.

## Consequences

- One documented post-apply command in the gateway path.
- The token-binding opt-out env var
  (`GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=false`) is set when
  the gateway is on, matching the codelab's `--allow-token-sharing`, so the
  Agent Identity's DPoP-bound tokens work with the SDKs the agent uses.
