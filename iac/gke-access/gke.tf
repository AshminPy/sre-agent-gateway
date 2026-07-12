# Optional demo GKE cluster in Project B. Skip (create_gke_cluster = false) to
# use an existing cluster — the cross-project grants in crossproject_iam.tf are
# applied either way so the agent can read it.

# APIs needed in Project B to run/read a GKE cluster.
resource "google_project_service" "b_apis" {
  for_each = var.create_gke_cluster ? toset([
    "container.googleapis.com",
    "compute.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
  ]) : toset([])

  project                    = var.project_b_id
  service                    = each.value
  disable_on_destroy         = false
  disable_dependent_services = false
}

# Minimal dedicated VPC for the demo cluster.
resource "google_compute_network" "gke" {
  count = var.create_gke_cluster ? 1 : 0

  project                 = var.project_b_id
  name                    = "sre-gke-vpc"
  auto_create_subnetworks = false

  depends_on = [google_project_service.b_apis]
}

resource "google_compute_subnetwork" "gke" {
  count = var.create_gke_cluster ? 1 : 0

  project                  = var.project_b_id
  name                     = "sre-gke-subnet"
  region                   = var.region
  network                  = google_compute_network.gke[0].id
  ip_cidr_range            = "10.60.0.0/20"
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.61.0.0/16"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.62.0.0/20"
  }
}

# GKE Autopilot cluster — the agent investigates workloads here (read-only) via
# GKE Remote MCP.
resource "google_container_cluster" "sre_test" {
  count = var.create_gke_cluster ? 1 : 0

  project             = var.project_b_id
  name                = var.gke_cluster_name
  location            = var.region
  enable_autopilot    = true
  deletion_protection = false

  network    = google_compute_network.gke[0].id
  subnetwork = google_compute_subnetwork.gke[0].id

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  depends_on = [google_project_service.b_apis]
}
