# VPC for the agent side (Project A). Provides Cloud NAT egress. (Previously
# also hosted a PSC-Interface subnet for Agent Gateway — removed 2026-08-07,
# see agent_gateway.tf's comment on google_network_services_agent_gateway.sre_egress
# for why. The custom Cloud Run MCP's own PSC/networking, if needed, belongs
# in cloudrun_mcp.tf, not here.)

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

