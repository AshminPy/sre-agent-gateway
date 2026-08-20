# ADR-002: Agent Identity and Agent Gateway binding

Status: Accepted — **implementation mechanism partially superseded 2026-08-10 by PR #93**

> **Note (added 2026-08-20):** the *decision* below stands unchanged — Agent Identity plus
> Agent Gateway binding is still how this works. What changed is the **mechanism**: the
> gateway binding was performed by `scripts/attach_gateway_to_engine.sh` when this ADR was
> written. PR #93 migrated it to Terraform-native `agent_gateway_config` (provider
> `google-beta` ≥ 7.40.0). The script is retained as a manual emergency rollback tool and
> is no longer invoked by CI. The atomic-PATCH requirement described below is still the
> reason the binding and the source deploy must be submitted together.

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

> **Update (2026-07-17): RESOLVED.** The `certificate verify failed` issue
> described below (originally logged 2026-07-13) is fixed and verified
> end-to-end. Two unrelated root causes, both closed — full writeup in
> [`archive/RESOLVED_2026-07-17_FINAL_RCA.md`](../archive/RESOLVED_2026-07-17_FINAL_RCA.md) and the management-facing
> [`archive/RESOLVED_2026-07-17_RCA_REPORT.md`](../archive/RESOLVED_2026-07-17_RCA_REPORT.md). Short version:
> 1. **The actual cause was never client-library choice** (the raw-genai-client
>    hypothesis below was a reasonable lead at the time, but wrong). The
>    gateway's TLS-inspection certificate is only trusted when the source
>    deploy and the `agentGatewayConfig` PATCH are submitted as one atomic
>    call — not two sequential calls, however close together. Confirmed via
>    Google's own reference `deploy_agent.py` pattern and a clean A/B test.
> 2. **`sreagent-t2-demo` needed a second, unrelated fix** even after #1 was
>    applied: the original engine resource had accumulated bad state from
>    repeated failed bind attempts made while #1 was still undiagnosed.
>    Fixed by recreating the engine (`terraform apply -replace=`).
> The "decisive next test" proposed below **was** eventually run (twice, in
> two different forms) — see `archive/RESOLVED_2026-07-17_FINAL_RCA.md`'s "t2-demo addendum" — and
> confirmed neither our code, our project, nor our gateway was at fault.
> The section below is preserved as the historical record of the
> investigation as it stood on 2026-07-13, not current guidance.

## Historical: agent→Vertex mTLS `certificate verify failed` (2026-07-13, resolved 2026-07-17)

Live validation into `sreagent-t2-demo` (gateway ON) hit a persistent TLS
`certificate verify failed` / `self signed certificate in certificate chain`
when the agent calls the Vertex mTLS endpoint **through** the gateway. It
survived every config variable we changed (Model Armor→IAP authz, global→regional
endpoint, PSC→no-PSC) **and** a fresh, churn-free gateway warmed ~3 hours. The
earlier "cold data plane, just wait" reading is therefore **not supported** by the
evidence.

What the evidence said at the time (2026-07-13) — since resolved, see the
update above:

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
  behind this gateway — which fits the zero-traces observation. *(This lead was
  investigated further and ruled out — see the update above; the real cause was
  the atomic-PATCH requirement, unrelated to client library choice.)*

The gateway-free path (`enable_agent_gateway=false`) is unaffected and works
immediately — which is why the repo keeps it as a first-class, tested mode.

[Troubleshoot Agent Gateway connectivity]: https://docs.cloud.google.com/gemini-enterprise-agent-platform/troubleshooting/troubleshoot-agent-gateway

## Consequences

- One documented post-apply command in the gateway path.
- The token-binding opt-out env var
  (`GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=false`) is set when
  the gateway is on, matching the codelab's `--allow-token-sharing`, so the
  Agent Identity's DPoP-bound tokens work with the SDKs the agent uses.
