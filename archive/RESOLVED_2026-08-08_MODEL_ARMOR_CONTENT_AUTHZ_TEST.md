# Model Armor CONTENT_AUTHZ live test — 2026-08-08

Real test evidence for the Google support case follow-up. Every command, output, and error below
is captured verbatim from this session — nothing paraphrased or assumed.

## Goal

Test enabling Model Armor's `CONTENT_AUTHZ` inspection at Agent Gateway, using Google's own
2026-08-07 support-case guidance: prompt-injection/jailbreak confidence set to `HIGH`, floor
settings checked first, per-path template separation already in place in this repo.

## Step 1 — Confirmed today's baseline (before any change)

Confidence level today, confirmed live via REST (not assumed from Terraform):

```bash
TOKEN="$(gcloud auth print-access-token)"
curl -sS -H "Authorization: Bearer ${TOKEN}" \
  "https://modelarmor.us-central1.rep.googleapis.com/v1/projects/sreagent-t2-demo/locations/us-central1/templates/sre-agent-request-guard"
```
```json
{
  "filterEnforcement": "ENABLED",
  "confidenceLevel": "MEDIUM_AND_ABOVE"
}
```

## Step 2 — Attempted to check org/project floor settings first, per Google's own gotcha

```bash
curl -sS -H "Authorization: Bearer ${TOKEN}" \
  "https://modelarmor.us-central1.rep.googleapis.com/v1/projects/sreagent-t2-demo/locations/us-central1/floorSetting"
```
```json
{"error": {"code": 500, "message": "An internal error has occurred (5308f8af-f131-4887-8183-beec39ea6ade)", "status": "INTERNAL"}}
```

```bash
curl -sS -H "Authorization: Bearer ${TOKEN}" \
  "https://modelarmor.us-central1.rep.googleapis.com/v1/organizations/1076201471152/locations/us-central1/floorSetting"
```
```json
{"error": {"code": 403, "message": "Permission 'modelarmor.floorSettings.get' denied on resource '//modelarmor.googleapis.com/organizations/1076201471152/locations/us-central1/floorSetting' ...", "status": "PERMISSION_DENIED"}}
```

**Result: could not verify floor settings.** Project-level returns a server-side `500 INTERNAL`
error (not a clean "not configured" response). Org-level returns a genuine `403
PERMISSION_DENIED` on `modelarmor.floorSettings.get` for our identity. Neither confirms nor
rules out an org floor override — flagging honestly, not assuming clear.

## Step 3 — Set confidence to HIGH (Terraform var, no code change needed)

`iac/agent/variables.tf`'s `model_armor_pi_confidence` was already a configurable variable
(default `MEDIUM_AND_ABOVE`, valid values `LOW_AND_ABOVE`/`MEDIUM_AND_ABOVE`/`HIGH`). Applied via
`-var="model_armor_pi_confidence=HIGH"`:

```
Plan: 2 to add, 1 to change, 0 to destroy.
  # google_model_armor_template.sre_agent_request will be updated in-place
```
Applied successfully — template's `confidenceLevel` confirmed `HIGH` at this point.

## Step 4 — Wired CONTENT_AUTHZ at the gateway (the actual blocker)

`agent_gateway.tf` already wires IAP's `REQUEST_AUTHZ` via a proven, working pattern:
`google_network_services_authz_extension` (service=`iap.googleapis.com`) +
`google_network_security_authz_policy` (policy_profile=`REQUEST_AUTHZ`). Mirrored the identical
pattern for Model Armor, per this repo's own `model_armor.tf` comment (written by an earlier
session) naming the expected fields:

```hcl
resource "google_network_services_authz_extension" "model_armor" {
  count    = local.gw_count
  provider = google-beta

  project   = var.project_a_id
  name      = "sre-agent-model-armor-authz"
  location  = var.region
  service   = "modelarmor.googleapis.com"
  timeout   = "2s"
  fail_open = var.authz_fail_open

  metadata = {
    request_template_id  = google_model_armor_template.sre_agent_request.template_id
    response_template_id = google_model_armor_template.sre_agent_response.template_id
  }

  depends_on = [google_project_service.apis]
}

resource "google_network_security_authz_policy" "model_armor" {
  count    = local.gw_count
  provider = google-beta

  project        = var.project_a_id
  name           = "sre-agent-model-armor-gateway-policy"
  location       = var.region
  policy_profile = "CONTENT_AUTHZ"
  action         = "CUSTOM"

  target {
    resources = [google_network_services_agent_gateway.sre_egress[0].id]
  }

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.model_armor[0].id]
    }
  }

  lifecycle {
    replace_triggered_by = [google_network_services_agent_gateway.sre_egress]
  }

  depends_on = [time_sleep.wait_for_gateway]
}
```

`terraform validate` passed clean (schema-valid field names). `terraform plan` was clean (2 to
add, 1 to change, 0 destroy — no surprises).

**`terraform apply` failed on the real API call:**

```
google_network_services_authz_extension.model_armor[0]: Creating...

Error: Error creating AuthzExtension: googleapi: Error 400: The request was invalid:
unsupported Google API for AuthzExtension: modelarmor.googleapis.com
Details:
[
  {
    "@type": "type.googleapis.com/google.rpc.BadRequest",
    "fieldViolations": [{"field": "authz_extension.service"}]
  },
  {"@type": "type.googleapis.com/google.rpc.RequestInfo", "requestId": "91f5982523cacf88"}
]
```

**Conclusion: `AuthzExtension`'s `service` field explicitly rejects `modelarmor.googleapis.com`**
(HTTP 400, `unsupported Google API for AuthzExtension`). This is not a permissions issue, not a
config typo — the API itself says this service value isn't supported for this resource type.
Whatever mechanism Google's own Agent Gateway console/gcloud flow uses to wire Model Armor
`CONTENT_AUTHZ` (per `docs.cloud.google.com/gemini-enterprise-agent-platform/govern/configure-model-armor`,
which describes only console/gcloud steps, no Terraform), it is **not** the same
`google_network_services_authz_extension` + `google_network_security_authz_policy` pattern used
for IAP `REQUEST_AUTHZ` — despite this repo's own pre-existing comment implying it was.

## Step 5 — Reverted everything

- Removed the two new resource blocks from `agent_gateway.tf` (never merged/committed).
- Re-applied without the `HIGH` override — confidence confirmed back to `MEDIUM_AND_ABOVE` via
  REST (Step 1's same query, re-run).
- `git status` clean — no stray infra changes left in the working tree.

## What this means for the Google reply

We could not complete the requested end-to-end test, because we hit a real, concrete
implementation gap first: **there is no documented, working Terraform (or otherwise
programmatic) path to wire Model Armor `CONTENT_AUTHZ` to Agent Gateway** that we could find —
the codelab only shows IAP, the Model Armor config doc only shows console/gcloud steps with no
resource-level detail, and our own attempt to mirror the IAP pattern was explicitly rejected by
the API. Worth asking Google directly for the exact `gcloud`/Terraform-equivalent mechanism,
since this blocks testing their confidence-tuning recommendation at the gateway layer entirely.
