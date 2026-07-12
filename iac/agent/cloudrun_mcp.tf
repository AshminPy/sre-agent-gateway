# Custom Cloud Run MCP server — OPTIONAL fallback to the Google-managed GKE
# Remote MCP. The agent uses GKE Remote MCP as its primary source and only
# falls back to this if configured. Enable with enable_custom_mcp = true.
#
# Build & push the image before enabling (see README / `make build-mcp`); until
# then custom_mcp_image is a Google placeholder so `terraform apply` succeeds.

# Artifact Registry repo for the MCP image (created regardless so `make build-mcp`
# has somewhere to push).
resource "google_artifact_registry_repository" "mcp" {
  count = var.enable_custom_mcp ? 1 : 0

  project       = var.project_a_id
  location      = var.region
  repository_id = "sre-agent-repo"
  format        = "DOCKER"
  description   = "Container images for the custom SRE MCP server"

  depends_on = [google_project_service.apis]
}

# Dedicated least-privilege runtime SA for the Cloud Run MCP service. It reads
# GKE resources cross-project in Project B (granted in the iac/gke-access stack
# by referencing this SA's email — see that stack's crossproject IAM).
resource "google_service_account" "mcp_runtime" {
  count = var.enable_custom_mcp ? 1 : 0

  project      = var.project_a_id
  account_id   = "sre-k8s-mcp-runtime"
  display_name = "SRE Custom MCP (Cloud Run) runtime identity"
}

resource "google_cloud_run_v2_service" "mcp" {
  count = var.enable_custom_mcp ? 1 : 0

  project             = var.project_a_id
  name                = "sre-k8s-mcp"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account = google_service_account.mcp_runtime[0].email

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    containers {
      image = var.custom_mcp_image
      ports { container_port = 8080 }

      env {
        name  = "PROJECT_ID"
        value = var.project_b_id
      }
    }
  }

  depends_on = [google_project_service.apis]
}
