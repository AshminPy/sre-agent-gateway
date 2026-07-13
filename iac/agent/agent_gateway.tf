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
# with NO networkConfig / NO PSC network attachment. The gateway reaches the
# public Google-API + GKE Remote MCP destinations over Google's backbone; a PSC
# egress attachment into a VPC is only for PRIVATE-VPC targets (not our case),
# and the codelab creates none.

resource "google_network_services_agent_gateway" "sre_egress" {
  count = local.gw_count

  project     = var.project_a_id
  name        = "sre-agent-egress"
  location    = var.region
  description = "Egress gateway — SRE agent to GKE Remote MCP and Google APIs"

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }

  # protocols=[MCP] per the official codelab: the gateway parses MCP request
  # attributes for authorization.
  protocols = ["MCP"]

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
  timeout   = "1s"
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

  depends_on = [time_sleep.wait_for_gateway]
}
