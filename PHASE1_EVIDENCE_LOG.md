# Phase 1 — Structured Evidence Log

Append-only. Each entry: timestamp, what was tested, exact command/config, exact result, interpretation.
See PHASE1_EXECUTION_STATE.md for the current-status summary this log backs.

---

## 2026-09-05 — CONTENT_AUTHZ provider capability check

**Test:** does the installed Terraform provider (google-beta 7.43.0) support Model Armor as a `google_network_services_authz_extension` service, and in what format?

**Command:**
```
terraform providers schema -json | jq '.provider_schemas["registry.terraform.io/hashicorp/google-beta"].resource_schemas.google_network_services_authz_extension.block.attributes.service'
```

**Result (verbatim):**
```
"The service that runs the extension.
The following values and formats are accepted:
* 'iap.googleapis.com' when the policyProfile is set to REQUEST_AUTHZ
* 'modelarmor.{{region}}.rep.googleapis.com' when the policyProfile is set to CONTENT_AUTHZ
* A fully qualified domain name that can be resolved by the dataplane
* Backend service resource URI ..."
```

**Interpretation:** The CURRENT provider version explicitly documents and supports `modelarmor.{region}.rep.googleapis.com` (the REGIONAL format) for CONTENT_AUTHZ. The archived 2026-08-08 failure used `service = "modelarmor.googleapis.com"` — the GENERIC, non-regional format — which the schema does NOT list as accepted. This strongly suggests the August failure was a service-string-format bug, not a genuine product/provider limitation. To be re-tested with the correct regional string.

## 2026-09-05 — CONTENT_AUTHZ built and applied (real, live, kept on feat/phase-1-release)

**Resources added** (`iac/agent/agent_gateway.tf`): `google_network_services_authz_extension.model_armor` (service=`modelarmor.us-central1.rep.googleapis.com`, fail_open=false, metadata references the SAME `sre_agent_request`/`sre_agent_response` templates the app layer uses), `google_network_security_authz_policy.model_armor` (policy_profile=CONTENT_AUTHZ, targets the existing gateway).

**terraform plan**: `2 to add, 0 to change, 0 to destroy` — isolated, no unexpected changes.
**terraform apply**: both resources created successfully — CONFIRMS the 2026-08-08 archived failure ("unsupported Google API for AuthzExtension") does NOT reproduce with the regional service string on the current provider (google-beta 7.43.0). Real IDs:
- `projects/sreagent-t2-demo/locations/us-central1/authzExtensions/sre-agent-model-armor-authz`
- (policy resource created immediately after, no errors)

**IAM**: confirmed live via `gcloud projects get-iam-policy sreagent-t2-demo` that `service-327234009108@gcp-sa-dep.iam.gserviceaccount.com` (the Service Extensions service agent, auto-provisioned by the extension's own creation — NOT something I created) already held `roles/serviceextensions.serviceAgent` by default. Added via Terraform (`google_project_iam_member`, 3 resources, `2 to add→3 to add, 0 to change, 0 to destroy` plan, applied clean): `roles/modelarmor.calloutUser`, `roles/serviceusage.serviceUsageConsumer`, `roles/modelarmor.user` — all granted to this service agent, confirmed NOT the agent runtime identity. Live re-check after apply: all 4 roles present on the correct principal.
