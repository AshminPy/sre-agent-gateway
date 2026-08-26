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
    "roles/aiplatform.agentDefaultAccess",     # Agent Runtime default access (official codelab grants this to the agent identity)
    "roles/serviceusage.serviceUsageConsumer", # quota / API access
    "roles/browser",                           # resourcemanager.projects.get (Agent Identity prereq)
    "roles/agentregistry.viewer",              # read Agent Registry (mcpServers discovery)
    "roles/logging.logWriter",                 # structured run logs
    "roles/cloudtrace.agent",                  # OpenTelemetry traces
    "roles/monitoring.metricWriter",           # metrics
  ]
}

resource "google_project_iam_member" "runtime_identity" {
  for_each = toset(local.runtime_project_roles)

  project = var.project_a_id
  role    = each.value
  member  = local.agent_identity_member
}

# App-level Model Armor sanitize calls (agent/main.py _sanitize) only run
# when MODEL_ARMOR_TEMPLATE is set, which is gateway-OFF only (see
# agent_engine.tf) — under the gateway, Model Armor content inspection would
# happen at the gateway layer instead. Grant matches that same condition;
# without it, sanitize_user_prompt/sanitize_model_response 403s in
# gateway-OFF mode.
resource "google_project_iam_member" "runtime_model_armor_user" {
  count = var.enable_agent_gateway ? 0 : 1

  project = var.project_a_id
  role    = "roles/modelarmor.user"
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

# issue #103 Slice 1: the Reasoning Engine platform service agent (already granted
# roles below for gateway networking) is the identity that writes long-running query
# job output during execution. Caller-side read/write (uploading the input blob,
# downloading the output blob) happens under the caller's own already-existing
# credentials via the installed SDK -- no separate grant needed for that here, since
# Slice 1 does not commit a personal identity into Terraform.
resource "google_storage_bucket_iam_member" "query_jobs_re_writer" {
  bucket = google_storage_bucket.query_jobs.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:service-${data.google_project.a.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
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

# ── Platform service agents for the gateway data plane ──────────────────────
#
# REMOVED 2026-08-26. Three project-level network/DNS grants used to live here:
#
#   gcp-sa-aiplatform      -> roles/compute.networkAdmin, roles/dns.peer
#   gcp-sa-aiplatform-re   -> roles/compute.networkAdmin, roles/dns.peer
#   gcp-sa-agentgateway    -> roles/dns.admin
#
# Why they are gone:
#
# 1. They existed to program a Private Service Connect data plane. This stack
#    no longer creates one — the PSC-I network_attachment (sre-agent-egress-na)
#    and its dedicated subnet were removed on 2026-08-07 (see
#    agent_gateway.tf's header). The grants outlived the thing they served.
# 2. Google's own docs scope these to the Agent Gateway service agent on the
#    SHARED VPC HOST project, and name roles/compute.networkUser for the
#    network attachment, with compute.networkAdmin only as a broader
#    alternative. gcp-sa-aiplatform and gcp-sa-aiplatform-re are not mentioned
#    in that context, and roles/dns.admin is not listed as a requirement for
#    gcp-sa-agentgateway (dns.peer is).
#    https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/set-up-vpc-connectivity
# 3. compute.networkAdmin and dns.admin are broad, project-level roles. The
#    company environment this stack is mirrored into (sre-agent-app-infra) is a
#    Shared VPC, where such grants need real justification. Both repos must
#    stay identical apart from variables, so the reduction is made here first
#    and validated live before it is copied over.
#
# roles/aiplatform.serviceAgent on gcp-sa-aiplatform is a separate, genuinely
# required grant and is untouched — the Agent Engine cannot be created without
# it.
#
# If the gateway ever fails to program its data path, add back ONLY the
# identity and role the error actually names. Prefer compute.networkUser over
# compute.networkAdmin, and dns.peer over dns.admin. Do not restore the whole
# block wholesale on a parity diff.

