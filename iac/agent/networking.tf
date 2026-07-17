# VPC for the agent side (Project A). Provides Cloud NAT egress and, when the
# gateway is enabled, hosts the gateway's PSC-Interface subnet (see agent_gateway.tf).

resource "google_compute_network" "agent" {
  project                 = var.project_a_id
  name                    = "sre-agent-vpc"
  auto_create_subnetworks = false

  depends_on = [google_project_service.apis]
}

resource "google_compute_subnetwork" "agent" {
  project                  = var.project_a_id
  name                     = "sre-agent-subnet"
  region                   = var.region
  network                  = google_compute_network.agent.id
  ip_cidr_range            = var.agent_subnet_cidr
  private_ip_google_access = true
}

resource "google_compute_router" "agent" {
  project = var.project_a_id
  name    = "sre-agent-router"
  region  = var.region
  network = google_compute_network.agent.id
}

resource "google_compute_router_nat" "agent" {
  project                            = var.project_a_id
  name                               = "sre-agent-nat"
  router                             = google_compute_router.agent.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# Dedicated subnet for the Agent Gateway's PSC-Interface network attachment.
# Added 2026-07-16 to test whether PSC-I's presence affected the gateway-bind
# failure under investigation. Confirmed NOT the cause (see FINAL_RCA.md) —
# the actual fix was unrelated (engine recreation). Not used for any real
# data-plane traffic (the agent's actual destinations are public Google APIs
# + GKE Remote MCP, reached over Google's backbone); kept because it's
# harmless and matches the vendored codelab reference's own module, which
# creates this unconditionally.
# Must not overlap 10.0.0.0/24 (agent subnet), 10.0.1.0/24, or 10.0.2.0/24 —
# documented Agent Gateway egress restriction.
resource "google_compute_subnetwork" "agent_gateway_psc" {
  count         = local.gw_count
  project       = var.project_a_id
  name          = "sre-agent-gateway-psc-subnet"
  region        = var.region
  network       = google_compute_network.agent.id
  ip_cidr_range = "10.20.0.0/28"
}
