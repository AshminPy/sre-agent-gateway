# Enable the Google Cloud APIs the agent stack needs in Project A.
#
# Bootstrap: serviceusage.googleapis.com must be enabled manually once before
# the first apply (see README prerequisites) — Terraform cannot enable the API
# it uses to enable APIs.

locals {
  # Always needed (agent runtime, model, storage, observability, registry).
  core_apis = [
    "aiplatform.googleapis.com",       # Vertex AI Agent Engine (reasoning engine, memory bank)
    "agentregistry.googleapis.com",    # Agent Registry (agent + MCP + endpoint registration)
    "modelarmor.googleapis.com",       # Model Armor (prompt/response safety filtering)
    "run.googleapis.com",              # Cloud Run (custom MCP fallback server)
    "artifactregistry.googleapis.com", # Artifact Registry (MCP container image)
    "storage.googleapis.com",          # GCS (evidence / eval / cluster-config buckets)
    "compute.googleapis.com",          # VPC / subnet / NAT (+ PSC attachment when gateway on)
    "monitoring.googleapis.com",       # Alert policies + notification channel
    "logging.googleapis.com",          # Log-based metrics + Log Analytics
    "cloudtrace.googleapis.com",       # OpenTelemetry traces
    "telemetry.googleapis.com",        # Agent Engine telemetry export
    "iam.googleapis.com",              # Service accounts + IAM
    "iamcredentials.googleapis.com",   # Token creation (WIF, impersonation)
    "sts.googleapis.com",              # Workload Identity Federation
    "cloudresourcemanager.googleapis.com",
    "dlp.googleapis.com", # Sensitive Data Protection (Model Armor SDP)
  ]

  # Additionally needed when the Agent Gateway is enabled.
  gateway_apis = [
    "networkservices.googleapis.com",   # Agent Gateway + authz extensions
    "networksecurity.googleapis.com",   # Authorization policies
    "iap.googleapis.com",               # IAP authz extension + agent-registry egress IAM
    "dns.googleapis.com",               # Gateway DNS peering
    "servicenetworking.googleapis.com", # PSC / private services backbone for the gateway data plane
    "observability.googleapis.com",     # Agent Gateway console observability tabs
  ]

  required_apis = toset(concat(local.core_apis, var.enable_agent_gateway ? local.gateway_apis : []))
}

resource "google_project_service" "apis" {
  for_each = local.required_apis

  project = var.project_a_id
  service = each.value

  # Keep APIs enabled if the resource is removed; don't disable on destroy
  # (other workloads in the project may depend on them).
  disable_on_destroy         = false
  disable_dependent_services = false
}

# Force-create the Vertex AI service agent (service-<num>@gcp-sa-aiplatform...).
# On a brand-new project, enabling aiplatform.googleapis.com does NOT immediately
# create this Google-managed identity, so IAM bindings that target it fail with
# "Service account ... does not exist" on the first apply. Creating it explicitly
# here (and depending the bindings on it) removes that race. Idempotent.
resource "google_project_service_identity" "aiplatform" {
  provider = google-beta
  project  = var.project_a_id
  service  = "aiplatform.googleapis.com"

  depends_on = [google_project_service.apis]
}
