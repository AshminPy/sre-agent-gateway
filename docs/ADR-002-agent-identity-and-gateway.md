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
serves traffic — during which egress calls can fail silently. If the agent's
calls fail right after enabling the gateway, wait and retry before debugging
config.

> **Caveat (2026-07-13):** we initially attributed a persistent
> `certificate verify failed` on the agent→Vertex mTLS endpoint
> (`us-central1-aiplatform.mtls.googleapis.com`) to this async warmth. That
> explanation no longer holds — see "Open item" below. Do not treat cert-verify
> failures as merely "not warm yet."

## Open item: agent→Vertex mTLS `certificate verify failed` (2026-07-13)

Live validation into `sreagent-t2-demo` (gateway ON) hit a persistent TLS
`certificate verify failed` / `self signed certificate in certificate chain`
when the agent calls the Vertex mTLS endpoint **through** the gateway. It
survived every config variable we changed (Model Armor→IAP authz, global→regional
endpoint, PSC→no-PSC) **and** a fresh, churn-free gateway warmed ~3 hours. The
earlier "cold data plane, just wait" reading is therefore **not supported** by the
evidence.

What the evidence now says:

- **No online footprint.** There is no public trace of this exact error anywhere.
  A universal Google bug in a documented combo (Agent Gateway + Agent Identity +
  Vertex) would have one. Its absence points at *our* setup, not the platform.
- **Google's own [Troubleshoot Agent Gateway connectivity] doc lists only
  registration/permission failure modes** — missing `roles/iap.egressor`,
  unregistered destination hostname, IAP blocking at startup — and explicitly
  states agents need **no special certificate setup** to trust the gateway. A
  cert-verify failure is not a documented/expected gateway failure mode.
- **Registration is not the gap.** The exact failing host
  `us-central1-aiplatform.mtls.googleapis.com` is confirmed registered in the
  Agent Registry.
- **The one genuinely-unique factor left:** this agent calls Vertex via a raw
  `genai.Client(vertexai=True, …)` (`agent/gemini_client.py`), whereas the proven
  codelab uses ADK `Agent(model=…)`. Almost nobody runs a raw google-genai client
  behind this gateway — which fits the zero-traces observation.

**Leading hypothesis:** the cert-verify is a property of *our* client
configuration, not Google-side data-plane warmth.

**Decisive next test (not yet run):** deploy this same repo into a brand-new
Project A. If it works there, the fault is specific to the `sreagent-t2-demo`
project's state; if it fails there too, the fault is our config/code (the
raw-genai-client hypothesis) and the fix lands in this repo. Until that test runs,
this item is **open**, not "expected platform behaviour."

The gateway-free path (`enable_agent_gateway=false`) is unaffected and works
immediately — which is why the repo keeps it as a first-class, tested mode.

[Troubleshoot Agent Gateway connectivity]: https://docs.cloud.google.com/gemini-enterprise-agent-platform/troubleshooting/troubleshoot-agent-gateway

## Consequences

- One documented post-apply command in the gateway path.
- The token-binding opt-out env var
  (`GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=false`) is set when
  the gateway is on, matching the codelab's `--allow-token-sharing`, so the
  Agent Identity's DPoP-bound tokens work with the SDKs the agent uses.
