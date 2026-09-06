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
  # Phase 1 fix (2026-09-04): was INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER, which
  # requires an Internal HTTP(S) Load Balancer + Serverless NEG pointed at this
  # service for ANYTHING to reach it -- none exists anywhere in this repo's
  # Terraform (see docs/architecture/mcp-architecture.md's own prior finding,
  # confirmed still true: `grep -rn serverless_neg\|forwarding_rule iac/`
  # returns nothing). Every caller, including Agent Gateway's own real request,
  # got a platform-level 404 -- reproduced live via a direct curl POST to
  # /mcp and confirmed in Agent Gateway's own request log (status 404,
  # authz result ALLOWED, correct hostname -- the request never reached the
  # container). Agent Gateway calls the service's PUBLIC .run.app hostname
  # with a Bearer identity token, the same pattern GKE Remote MCP's own
  # Google-managed public endpoint uses -- INTERNAL_LOAD_BALANCER was never
  # going to work for that call shape without also re-pointing Agent
  # Gateway's route at an internal LB, a materially bigger change. IAM
  # authorization is unchanged and unaffected: only the AGENT_IDENTITY
  # principal holds roles/run.invoker (google_cloud_run_v2_service_iam_member
  # .runtime_invoke_mcp below) -- no allUsers/allAuthenticatedUsers grant
  # exists, and per Cloud Run's own docs, ingress and IAM authorization are
  # enforced independently: opening ingress does not weaken or bypass IAM.
  ingress = "INGRESS_TRAFFIC_ALL"

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

      dynamic "env" {
        for_each = var.custom_mcp_kube_context != "" ? [var.custom_mcp_kube_context] : []
        content {
          name  = "K8S_MCP_KUBE_CONTEXT"
          value = env.value
        }
      }

      # POC (#203 follow-on, 2026-09-06): application-level Model Armor
      # response sanitization — see mcp/response_guard.py. REGION here is
      # this stack's own region (Model Armor templates live in project_a),
      # not project_b's region.
      env {
        name  = "REGION"
        value = var.region
      }
      env {
        name  = "MODEL_ARMOR_RESPONSE_TEMPLATE"
        value = google_model_armor_template.sre_agent_response.name
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# POC (#203 follow-on, 2026-09-06): lets the MCP runtime SA call Model
# Armor's sanitizeModelResponse API directly (application-level check,
# response_guard.py) against project_a's own sre_agent_response template.
# Distinct from google_project_iam_member.gateway_service_agent_model_armor_*
# in model_armor.tf, which grants the GATEWAY's own service agent (a
# different, Google-internal principal) access for the CONTENT_AUTHZ path —
# this grants OUR service's own runtime identity for the new direct-call path.
resource "google_project_iam_member" "mcp_runtime_model_armor_user" {
  count   = var.enable_custom_mcp ? 1 : 0
  project = var.project_a_id
  role    = "roles/modelarmor.user"
  member  = "serviceAccount:${google_service_account.mcp_runtime[0].email}"
}
