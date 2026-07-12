# CI/CD deploy identity: one Workload Identity Federation service account in
# Project A that GitHub Actions impersonates (no long-lived keys). It holds a
# scoped, least-privilege set of roles in Project A (below) and a tiny set in
# Project B (granted in the iac/gke-access stack, which takes this SA's email).
#
# Running Terraform locally with your own `gcloud auth application-default
# login` credentials does NOT require this — it is only for automated CI.

resource "google_service_account" "deployer" {
  project      = var.project_a_id
  account_id   = "sre-agent-deployer"
  display_name = "SRE Agent CI/CD deployer (WIF)"
}

# ── Scoped deployer roles in Project A (least privilege — no editor/owner) ──
locals {
  # Minimum roles to create everything this stack manages. See
  # docs/least-privilege-iam.md for the per-role justification.
  deployer_a_roles = [
    "roles/serviceusage.serviceUsageAdmin",  # enable required APIs
    "roles/iam.serviceAccountAdmin",         # create runtime/MCP SAs
    "roles/iam.serviceAccountUser",          # attach SAs (Cloud Run)
    "roles/iam.workloadIdentityPoolAdmin",   # manage the WIF pool/provider
    "roles/resourcemanager.projectIamAdmin", # set project IAM policy (scoped to A)
    "roles/compute.networkAdmin",            # VPC/subnet/NAT/firewall + PSC attachment
    "roles/aiplatform.admin",                # create reasoning engines + memory bank
    "roles/networkservices.editor",          # Agent Gateway + authz extensions
    "roles/networksecurity.editor",          # authorization policies
    "roles/modelarmor.admin",                # Model Armor template
    "roles/run.admin",                       # Cloud Run MCP fallback
    "roles/artifactregistry.admin",          # AR repo + image push
    "roles/monitoring.editor",               # alert policies + channel
    "roles/logging.configWriter",            # log-based metrics + Log Analytics bucket config
    "roles/storage.admin",                   # create/manage the agent's GCS buckets ⚑
    "roles/iap.admin",                       # set iap.egressor on the agent-registry resource; the only predefined role granting iap.webServiceVersions.setIamPolicy ⚑
  ]
}

resource "google_project_iam_member" "deployer_a" {
  for_each = toset(local.deployer_a_roles)

  project = var.project_a_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

# Object access to the Terraform state bucket (bucket-level, not project-wide).
resource "google_storage_bucket_iam_member" "deployer_tfstate" {
  count = var.tfstate_bucket != "" ? 1 : 0

  bucket = var.tfstate_bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.deployer.email}"
}

# ── Workload Identity Federation for GitHub Actions ────────────────────────
resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_a_id
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions"

  depends_on = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_a_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  # Only the named repository may mint tokens for this provider.
  attribute_condition = "assertion.repository == \"${var.github_repo}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# Let workloads from the named repo impersonate the deployer SA.
resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}
