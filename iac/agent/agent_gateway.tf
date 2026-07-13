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

# ── Dedicated subnet + PSC-Interface network attachment ────────────────────
# The gateway's egress data plane connects into this VPC via a PSC interface;
# the firewall below admits it. Required for the data plane to serve traffic.

resource "google_compute_subnetwork" "agent_gateway_psc" {
  count = local.gw_count

  project       = var.project_a_id
  name          = "agent-gateway-psc-subnet"
  region        = var.region
  network       = google_compute_network.agent.id
  ip_cidr_range = var.agent_gateway_subnet_cidr
}

resource "google_compute_network_attachment" "agent_gateway" {
  count = local.gw_count

  project               = var.project_a_id
  name                  = "agent-gateway-attachment"
  region                = var.region
  connection_preference = "ACCEPT_AUTOMATIC"
  subnetworks           = [google_compute_subnetwork.agent_gateway_psc[0].self_link]
}

# Admit the gateway's PSC interface on 443. Without this the data plane cannot
# complete connections.
resource "google_compute_firewall" "agent_gateway_allow_psc_i" {
  count = local.gw_count

  project       = var.project_a_id
  name          = "agent-gateway-allow-psc-i"
  network       = google_compute_network.agent.id
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = [var.agent_gateway_subnet_cidr]

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }
}

# ── The gateway ────────────────────────────────────────────────────────────

resource "google_network_services_agent_gateway" "sre_egress" {
  count = local.gw_count

  project     = var.project_a_id
  name        = "sre-agent-egress"
  location    = var.region
  description = "Egress gateway — SRE agent to GKE Remote MCP and Google APIs"

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }

  # The gateway data plane speaks these protocols; MCP is required so it can
  # decode tool calls. An empty list leaves the data plane unable to serve.
  protocols = ["MCP"]

  registries = [local.registry_uri]

  network_config {
    egress {
      network_attachment = google_compute_network_attachment.agent_gateway[0].id
    }
  }

  depends_on = [
    google_project_service.apis,
    google_compute_firewall.agent_gateway_allow_psc_i,
  ]
}

# Avoid a race where the authz policies are created before the gateway is ready.
resource "time_sleep" "wait_for_gateway" {
  count           = local.gw_count
  create_duration = "30s"
  depends_on      = [google_network_services_agent_gateway.sre_egress]
}

# ── IAP request authorization (REQUEST_AUTHZ) ──────────────────────────────

resource "google_network_services_authz_extension" "iap" {
  count    = local.gw_count
  provider = google-beta

  project   = var.project_a_id
  name      = "sre-agent-iap-authz"
  location  = var.region
  service   = "iap.googleapis.com"
  timeout   = "1s"
  fail_open = var.authz_fail_open

  # iapPolicyVersion is always required; iamEnforcementMode is added only in
  # DRY_RUN (logs decisions without blocking). Omit it to enforce.
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

# ── Model Armor content authorization (CONTENT_AUTHZ) — defense in depth ────

resource "google_network_services_authz_extension" "model_armor" {
  count    = local.gw_count
  provider = google-beta

  project   = var.project_a_id
  name      = "sre-agent-ma-authz"
  location  = var.region
  service   = "modelarmor.${var.region}.rep.googleapis.com"
  timeout   = "2s"
  fail_open = var.authz_fail_open

  metadata = {
    "model_armor_settings" = jsonencode([{
      request_template_id  = google_model_armor_template.sre_agent.id
      response_template_id = google_model_armor_template.sre_agent.id
    }])
  }

  depends_on = [google_model_armor_template.sre_agent]
}

resource "google_network_security_authz_policy" "model_armor" {
  count    = local.gw_count
  provider = google-beta

  project        = var.project_a_id
  name           = "sre-agent-ma-gateway-policy"
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

  # Attaching an authz policy updates the gateway's tenant configuration, and the
  # gateway rejects two concurrent tenant-config updates ("resource is being
  # created and can not be updated yet", ABORTED). Serialize after the IAP policy
  # so the two attach one at a time.
  depends_on = [
    time_sleep.wait_for_gateway,
    google_network_security_authz_policy.iap,
  ]
}
