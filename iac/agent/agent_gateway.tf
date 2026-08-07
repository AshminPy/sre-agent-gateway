# Agent Gateway — egress governance for the agent (AGENT_TO_ANYWHERE).
#
# All resources here are gated on var.enable_agent_gateway. When enabled, the
# gateway decodes and authorizes the agent's outbound MCP tool calls and can
# inspect content via Model Armor. The engine is attached to the gateway by a
# post-apply script (scripts/attach_gateway_to_engine.sh) because the reasoning
# engine's agent_gateway_config field is not yet exposed by the Terraform provider.
#
# Data-plane provisioning of a new gateway is asynchronous on Google's side and
# can take a while before traffic flows — this is expected (see docs/ADR-002).

locals {
  gw_count = var.enable_agent_gateway ? 1 : 0
}

# ── The gateway ────────────────────────────────────────────────────────────
# Per the official codelab (agw-cuj-arun-egress-gmcp): a Google-managed gateway
# reaching the public Google-API + GKE Remote MCP destinations over Google's
# backbone. A PSC egress attachment into a VPC is Google-documented as
# OPTIONAL ("Optional: Configure VPC connectivity" — set-up-agent-gateway
# docs), scoped to private-VPC targets only, which this deployment doesn't
# use — the codelab reference itself creates none.
#
# 2026-08-07: removed the PSC-I network_attachment (sre-agent-egress-na) and
# its dedicated subnet (networking.tf's agent_gateway_psc) that were added
# 2026-07-16/17 purely to test an unrelated gateway-bind hypothesis — already
# confirmed NOT the cause that same investigation (FINAL_RCA.md /
# TROUBLESHOOTING_LOG.md; real cause was accumulated engine-side state, fixed
# by engine recreation). This deployment's actual traffic (Google APIs + GKE
# Remote MCP) needs no VPC connectivity per this comment block's own citation
# above; re-verified live with a real agent invocation right after this
# change deployed (not assumed) — see TROUBLESHOOTING_LOG.md for that result.
# Removing this now — rather than leaving it as "harmless" — because the
# custom Cloud Run MCP target (a real private-VPC destination,
# INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER) needs its OWN correctly-scoped PSC
# setup, and this generic leftover attachment was not that (no Internal
# Application Load Balancer / Serverless NEG behind it, so it couldn't have
# reached Cloud Run either way) — see cloudrun_mcp.tf for that follow-up work.
resource "google_network_services_agent_gateway" "sre_egress" {
  count = local.gw_count

  project  = var.project_a_id
  name     = "sre-agent-egress"
  location = var.region
  # description and protocols=["MCP"] removed 2026-07-16 to exact-match
  # sreagent-cleanroom-test's working gateway (which has neither field set) —
  # see CURRENT_STATE.md "Proposed change". protocols=["MCP"] was previously
  # believed required per the official codelab (agw-cuj-arun-egress-gmcp) for
  # MCP request-attribute parsing; testing empirically rather than assuming,
  # per direct instruction to match the working project exactly first.

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }

  registries = [local.registry_uri]

  depends_on = [google_project_service.apis]
}

# Avoid a race where the authz policies are created before the gateway is ready.
resource "time_sleep" "wait_for_gateway" {
  count           = local.gw_count
  create_duration = "30s"
  depends_on      = [google_network_services_agent_gateway.sre_egress]
}

# ── IAP request authorization (REQUEST_AUTHZ) — the official codelab's governance ─
#
# Per the official codelab (agw-cuj-arun-egress-gmcp), the gateway is governed by
# an IAP REQUEST_AUTHZ extension: it authorizes each request by evaluating the
# agent identity's IAM policy on the target (the `iap.egressor` bindings in
# iap_egressor.tf). This is HEADER/attribute-based — the gateway does NOT
# TLS-terminate or inspect payload content, so there is no MITM (and no
# cert-verify failure on the agent's mutual-TLS Vertex endpoint). DRY_RUN logs
# decisions without blocking; switch to enforce (null enforcement mode,
# fail_open=false) once validated.
resource "google_network_services_authz_extension" "iap" {
  count    = local.gw_count
  provider = google-beta

  project   = var.project_a_id
  name      = "sre-agent-iap-authz"
  location  = var.region
  service   = "iap.googleapis.com"
  timeout   = "2s" # was "1s" — exact-matched to sreagent-cleanroom-test 2026-07-16
  fail_open = var.authz_fail_open

  metadata = merge(
    { iapPolicyVersion = "V1" },
    var.iap_iam_enforcement_mode != null ? { iamEnforcementMode = var.iap_iam_enforcement_mode } : {},
  )

  depends_on = [google_project_service.apis]
}

resource "google_network_security_authz_policy" "iap" {
  count    = local.gw_count
  provider = google-beta

  project        = var.project_a_id
  name           = "sre-agent-iap-gateway-policy"
  location       = var.region
  policy_profile = "REQUEST_AUTHZ"
  action         = "CUSTOM"

  target {
    resources = [google_network_services_agent_gateway.sre_egress[0].id]
  }

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.iap[0].id]
    }
  }

  # The gateway's id string is stable across a destroy+recreate (same name),
  # so Terraform can't see that this policy must be detached first. Without
  # this, a gateway replace hits "already being used by" (see docs/ADR-002).
  lifecycle {
    replace_triggered_by = [google_network_services_agent_gateway.sre_egress]
  }

  depends_on = [time_sleep.wait_for_gateway]
}
