# Google Cloud Support Case Draft — Model Armor on Vertex AI Agent Engine + Agent Gateway

> Draft for a Google Cloud support case. Not yet submitted. Compiled 2026-08-04 from this
> repo's own investigation records (`TROUBLESHOOTING_LOG.md`, `FINAL_RCA.md`, `RCA_REPORT.md`)
> — every quote/error below is copied verbatim from those files, not reconstructed from memory.
> No screenshots exist in this repo for this investigation — only text log output, included
> below. If you have console screenshots to add, attach them to the case separately.

---

## Subject

Model Armor on Agent Gateway (CONTENT_AUTHZ) appears incompatible with Agent Identity's default
mTLS connection to Vertex AI, and Model Armor's prompt-injection filter produces false positives
on benign Kubernetes log content — need guidance on supported configuration.

## Summary

We operate a Vertex AI Agent Engine (LangGraph-based SRE investigation agent) using
**Agent Identity** and an **Agent Gateway**. We want to enable **Model Armor** for
prompt-injection / content safety, per our internal security team's requirement before
production launch. We've hit two distinct problems and could not resolve either after
significant internal investigation:

1. Model Armor's own content filter flags completely benign Kubernetes incident text as a
   prompt-injection attack (false positive).
2. Enabling Model Armor's `CONTENT_AUTHZ` policy at the Agent Gateway coincides with — though we
   could not conclusively prove causes — persistent TLS certificate verification failures when
   our agent calls Vertex AI (Gemini) through the gateway.

We reviewed all Google documentation we could find on this (list below) and found **no
documented relationship between Model Armor and the mTLS trust chain used by Agent Identity** —
we believe this is a genuine documentation gap, not something we missed.

## Environment

- **Product:** Vertex AI Agent Engine (Reasoning Engine), Agent Identity, Agent Gateway, Model Armor
- **Agent framework:** LangGraph (Python), deployed via `google-genai` SDK (`google.genai.Client`)
- **Region:** `us-central1`
- **Test projects used during investigation:** `sreagent-cleanroom-test`, `sreagent-t2-demo`
  (both Google Cloud projects we own; happy to share project IDs/support-eligible details
  directly with the assigned engineer)
- **Repo (private, can share access on request):** `AshminPy/sre-agent-gateway`

---

## Problem 1 — Model Armor false-positive on benign SRE query

**Reproduction:** invoked the agent with the query:

```
Pod imagepull-pod in test-incidents cannot pull its image. Investigate.
```

**Result (verbatim from our logs, `TROUBLESHOOTING_LOG.md` line 735):**

```
⚠ BLOCKED by Model Armor: Input blocked by safety filter (prompt injection or harmful content detected)
```

- Traced to our own app-level `sanitize_user_prompt` call (client-side, active when the gateway
  is off and `MODEL_ARMOR_TEMPLATE` env is set).
- Confidence threshold configured: **`MEDIUM_AND_ABOVE`** (our `model_armor_pi_confidence`
  Terraform variable — Google-standard threshold value, not a custom one).
- **Reproducible on 2 separate attempts** — not a transient/warm-up issue.
- The text contains no instructions, role-play framing, or anything we can identify as
  injection-like — it's a plain incident description.

**Question for Google:** why does Model Armor's prompt-injection classifier flag this text at
`MEDIUM_AND_ABOVE`, and is there a recommended confidence threshold or template configuration
for SRE/observability use cases where the input is raw log/event text rather than
user-authored chat?

---

## Problem 2 — Model Armor at the gateway coincides with mTLS certificate failures

**Background:** Agent Identity's documented default is to use mTLS with X.509 certificates when
calling Google Cloud APIs directly (confirmed in your own docs, quoted below). When Model
Armor's `CONTENT_AUTHZ` policy is attached to our Agent Gateway, the gateway must perform TLS
inspection to read the traffic it's authorizing — which requires terminating/re-establishing
that connection with its own gateway-provisioned CA.

**Errors observed (verbatim, from three different attempts across the investigation — note the
error signature changed as we adjusted configuration, listed here in the order encountered):**

```
"error": "HTTPSConnectionPool(host='us-central1-aiplatform.mtls.googleapis.com', port=443):
Max retries exceeded ... SSLError(SysCallError(-1, 'Unexpected EOF'))"
```

```
{"status": "failed", "error": "HTTPSConnectionPool(host='us-central1-aiplatform.mtls.googleapis.com',
port=443): ... SSLError(SSLError(\"bad handshake: Error([('SSL routines', '', 'certificate verify failed')])\"))"}
```

```
{"status": "failed", "error": "HTTPSConnectionPool(host='us-central1-aiplatform.mtls.googleapis.com',
port=443): ... SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate
verify failed: self-signed certificate in certificate chain (_ssl.c:1016)'))"}
```

**What we tried (none of the following, individually or combined, resolved the certificate
error):**
1. Explicitly registering both the plain and `.mtls.` Vertex AI endpoint hostnames in Agent
   Registry.
2. Removing a `base_url` override in our code that attempted to force the plain (non-mTLS)
   endpoint (`agent/gemini_client.py`) — we later confirmed via source inspection that
   `google.genai.Client` has no code path that would ever request the `.mtls.` hostname itself,
   meaning something below our application layer (most likely Agent Identity's own
   runtime-injected transport) is substituting the mTLS hostname regardless of what our code
   requests.
3. Adding a defensive `pyopenssl` import.
4. Removing Model Armor (`MODEL_ARMOR_TEMPLATE` env var) entirely from the engine.

We also recreated the underlying Reasoning Engine resource entirely at one point (unrelated
follow-up investigation, documented separately) and confirmed engine identity/state was not the
cause of this specific certificate error.

**Documentation we reviewed** (fetched directly, quotes below are verbatim):

1. `docs.cloud.google.com/.../troubleshooting/agent-deployment`
2. `docs.cloud.google.com/.../scale/runtime/agent-gateway-runtime-deploy`
3. `docs.cloud.google.com/.../govern/gateways/set-up-agent-gateway`
4. `docs.cloud.google.com/.../govern/configure-model-armor`
5. `docs.cloud.google.com/.../govern/agent-identity-overview`
6. `docs.cloud.google.com/.../scale/runtime/agent-identity`
7. `docs.cloud.google.com/.../govern/gateways/delegate-authorization`

**Relevant quotes found:**

- Model Armor is explicitly optional: *"(Optional) If your deployment requires safeguarding
  against prompt injection attacks..."* and *"Optional: In the AI Security section, configure
  additional security"* (set-up-agent-gateway doc).
- mTLS is the documented default for Agent Identity, not something we opted into: *"By default,
  agent identities use mutual TLS (mTLS) with X.509 certificates when communicating directly
  with Google Cloud APIs"* (agent-identity-overview); *"We also auto-provision and manage an
  x509 certificate on the agent with the same identity for secure authentication"* and *"Agent
  identity credentials are secured by default through a Google-managed Context-Aware Access
  (CAA) policy. This policy enforces mTLS binding..."* (scale/runtime/agent-identity).
- The only documented related opt-out, `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES`,
  is already set to `false` on our engine — but the doc frames this as controlling SDK
  credential/token sharing, not endpoint-hostname selection, and doesn't clarify whether it
  affects which hostname (plain vs `.mtls.`) gets targeted.
- `delegate-authorization` (the doc covering how Model Armor wires into gateway authorization)
  describes gateway-to-Model-Armor communication using its own TLS, but **says nothing about
  whether/how this affects the agent's own mTLS connection to Vertex AI.**

**We could not find any documentation describing the relationship between Model Armor
`CONTENT_AUTHZ` at the gateway and Agent Identity's mTLS connection to Vertex AI. We believe
this is a genuine gap, not something we overlooked** — we searched all 7 documents above
specifically for this relationship.

**Questions for Google:**
1. Is there a documented or supported way to run Model Armor `CONTENT_AUTHZ` at the Agent
   Gateway alongside Agent Identity's default mTLS connection to Vertex AI, without triggering
   certificate verification failures?
2. Is the mTLS handshake intended to terminate *at* the Agent Gateway (which would then need to
   be properly positioned/trusted for that specific call path), rather than at a direct
   Vertex AI endpoint? If so, what configuration achieves that for Agent-Identity-to-Vertex-AI
   calls specifically (as opposed to Agent-to-MCP/VPC destination calls, which your docs cover
   more thoroughly)?
3. Is there a supported, documented way to force the plain (non-mTLS) Vertex AI endpoint for an
   Agent Identity engine? We could not find one.

---

## What we are NOT reporting as a bug (ruled out, don't need Google's help here)

- Model Armor requiring `roles/modelarmor.user` — expected, unrelated to the TLS issue.
- Gateway `networkConfig`/PSC-interface differences we found between two of our test
  projects — investigated separately, not related to Model Armor.
- An unrelated, separately-resolved issue where our own Reasoning Engine had accumulated bad
  backend state from repeated failed gateway-bind attempts (fixed by recreating the engine
  resource) — a different failure signature (`{"code": 3, "message": "The Reasoning Engine
  failed to be updated."}`) than the certificate errors above, and already resolved on our end.

## Current workaround

Model Armor is disabled everywhere (both at the gateway and at the application level) until we
get guidance from Google. We're relying on other controls in the meantime: read-only tool
allowlist (no destructive Kubernetes verbs permitted), IAP `REQUEST_AUTHZ` on the gateway,
least-privilege IAM via Agent Identity, structured/bounded agent responses, and human review of
all output before any action is taken. This is a temporary posture — our security team requires
Model Armor resolved before production launch.

## Attachments to prepare before filing

- [ ] Full text of `TROUBLESHOOTING_LOG.md` sections referenced above (or relevant excerpts)
- [ ] `iac/agent/agent_gateway.tf`, `iac/agent/model_armor.tf` (current, Model-Armor-disabled config)
- [ ] Console screenshots of the Agent Gateway config and Model Armor template config (not
      captured in this repo — take fresh screenshots before filing)
- [ ] Project IDs / resource names (share directly with Google support, not in this file)
