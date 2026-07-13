# IAM for the agent runtime identity and the Google-managed platform service
# agents. Least privilege throughout: the runtime identity gets only the roles
# it uses, scoped to resources (bucket/service level) where possible. Its
# cross-project GKE access lives in the separate iac/gke-access stack.
#
# Full justified role tables: docs/least-privilege-iam.md.

# ── Runtime Agent Identity: project-level roles ────────────────────────────
locals {
  # Each role below is used by the running agent; see docs/least-privilege-iam.md
  # for the per-role justification.
  runtime_project_roles = [
    "roles/aiplatform.expressUser",            # inference / sessions / memory (Agent Identity baseline)
    "roles/aiplatform.user",                   # Memory Bank generate/retrieve
    "roles/serviceusage.serviceUsageConsumer", # quota / API access
    "roles/browser",                           # resourcemanager.projects.get (Agent Identity prereq)
    "roles/agentregistry.viewer",              # read Agent Registry (mcpServers discovery)
    "roles/logging.logWriter",                 # structured run logs
    "roles/cloudtrace.agent",                  # OpenTelemetry traces
    "roles/monitoring.metricWriter",           # metrics
    "roles/modelarmor.user",                   # app-layer prompt/response sanitize
  ]
}

resource "google_project_iam_member" "runtime_identity" {
  for_each = toset(local.runtime_project_roles)

  project = var.project_a_id
  role    = each.value
  member  = local.agent_identity_member
}

# ── Runtime Agent Identity: bucket-level roles (not project-wide storage) ───
resource "google_storage_bucket_iam_member" "runtime_evidence_writer" {
  bucket = google_storage_bucket.evidence.name
  role   = "roles/storage.objectCreator"
  member = local.agent_identity_member
}

resource "google_storage_bucket_iam_member" "runtime_evidence_reader" {
  bucket = google_storage_bucket.evidence.name
  role   = "roles/storage.objectViewer"
  member = local.agent_identity_member
}

resource "google_storage_bucket_iam_member" "runtime_eval_writer" {
  bucket = google_storage_bucket.eval.name
  role   = "roles/storage.objectCreator"
  member = local.agent_identity_member
}

resource "google_storage_bucket_iam_member" "runtime_eval_reader" {
  bucket = google_storage_bucket.eval.name
  role   = "roles/storage.objectViewer"
  member = local.agent_identity_member
}

resource "google_storage_bucket_iam_member" "runtime_cluster_config_reader" {
  bucket = google_storage_bucket.cluster_config.name
  role   = "roles/storage.objectViewer"
  member = local.agent_identity_member
}

# ── Runtime Agent Identity: resource-level Cloud Run invoker (fallback MCP) ──
resource "google_cloud_run_v2_service_iam_member" "runtime_invoke_mcp" {
  count = var.enable_custom_mcp ? 1 : 0

  project  = var.project_a_id
  location = var.region
  name     = google_cloud_run_v2_service.mcp[0].name
  role     = "roles/run.invoker"
  member   = local.agent_identity_member
}

# ── Platform service agents for the gateway data plane (gated on gateway) ───
# These are Google-managed identities that provision the gateway's networking
# and DNS. Roles are the minimum documented for the gateway to function.

# Vertex AI + Reasoning Engine service agents need to program networking/DNS for
# the gateway's PSC data plane.
resource "google_project_iam_member" "aiplatform_sa_network" {
  for_each = var.enable_agent_gateway ? toset(["roles/compute.networkAdmin", "roles/dns.peer"]) : toset([])

  project = var.project_a_id
  role    = each.value
  member  = "serviceAccount:${google_project_service_identity.aiplatform.email}"

  depends_on = [google_project_service_identity.aiplatform]
}

resource "google_project_iam_member" "aiplatform_re_sa_network" {
  for_each = var.enable_agent_gateway ? toset(["roles/compute.networkAdmin", "roles/dns.peer"]) : toset([])

  project = var.project_a_id
  role    = each.value
  member  = "serviceAccount:service-${data.google_project.a.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

  # The -re service agent is created when the first reasoning engine is deployed.
  depends_on = [google_vertex_ai_reasoning_engine.sre_agent]
}

# Agent Gateway P4SA programs DNS for the data path.
resource "google_project_iam_member" "agentgateway_p4sa_dns" {
  count = var.enable_agent_gateway ? 1 : 0

  project = var.project_a_id
  role    = "roles/dns.admin"
  member  = "serviceAccount:service-${data.google_project.a.number}@gcp-sa-agentgateway.iam.gserviceaccount.com"

  depends_on = [google_network_services_agent_gateway.sre_egress]
}

