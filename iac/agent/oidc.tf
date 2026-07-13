# CI/CD deploy identity: one Workload Identity Federation service account in
# Project A that GitHub Actions impersonates (no long-lived keys). It holds a
# scoped, least-privilege set of roles in Project A (below) and a tiny set in
# Project B (granted in the iac/gke-access stack, which takes this SA's email).
#
# Running Terraform locally with your own `gcloud auth application-default
# login` credentials does NOT require this — it is only for automated CI.
#
# All resources here are gated on var.create_wif. Set it false when the deployer
# identity is bootstrapped out-of-band by scripts/bootstrap_wif.sh (the git-driven
# flow — CI authenticates AS this identity, so it cannot create it). The outputs
# wif_provider / deployer_sa_email resolve to the deterministic bootstrapped names
# in that mode, so downstream consumers work either way.

resource "google_service_account" "deployer" {
  count = var.create_wif ? 1 : 0

  project      = var.project_a_id
  account_id   = "sre-agent-deployer"
  display_name = "SRE Agent CI/CD deployer (WIF)"
}

# ── Deployer roles in Project A ─────────────────────────────────────────────
# Mirrors the official codelab operator roles (agw-cuj-arun-egress-gmcp step 2:
# networkservices.admin, serviceextensions.admin, networksecurity.admin,
# agentregistry.admin, aiplatform.admin, iap.admin, storage.admin,
# serviceusage.serviceUsageAdmin) plus the roles this SA needs to manage its own
# WIF/IAM/supporting infra. NOTE: intentionally NOT least-privilege right now —
# tighten later (see docs/least-privilege-iam.md).
locals {
  deployer_a_roles = [
    "roles/serviceusage.serviceUsageAdmin",  # enable required APIs
    "roles/iam.serviceAccountAdmin",         # create runtime/MCP SAs
    "roles/iam.serviceAccountUser",          # attach SAs (Cloud Run)
    "roles/iam.workloadIdentityPoolAdmin",   # manage the WIF pool/provider
    "roles/resourcemanager.projectIamAdmin", # set project IAM policy (scoped to A)
    "roles/compute.networkAdmin",            # VPC/subnet/NAT/routes + PSC attachment
    "roles/compute.securityAdmin",           # firewall rules (networkAdmin lacks compute.firewalls.create) ⚑
    "roles/aiplatform.admin",                # create reasoning engines + memory bank
    "roles/networkservices.admin",           # Agent Gateway (codelab operator role)
    "roles/networksecurity.admin",           # authorization policies (codelab operator role)
    "roles/agentregistry.admin",             # Agent Registry catalog + gateway registry validation (codelab operator role)
    "roles/modelarmor.admin",                # Model Armor templates (app-level, gateway-off)
    "roles/run.admin",                       # Cloud Run MCP fallback
    "roles/artifactregistry.admin",          # AR repo + image push
    "roles/monitoring.editor",               # alert policies + channel
    "roles/logging.configWriter",            # log-based metrics + Log Analytics bucket config
    "roles/storage.admin",                   # create/manage the agent's GCS buckets ⚑
    "roles/iap.admin",                       # set iap.egressor on the agent-registry resource; the only predefined role granting iap.webServiceVersions.setIamPolicy ⚑
  ]
}

resource "google_project_iam_member" "deployer_a" {
  for_each = var.create_wif ? toset(local.deployer_a_roles) : toset([])

  project = var.project_a_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deployer[0].email}"
}

# Object access to the Terraform state bucket (bucket-level, not project-wide).
resource "google_storage_bucket_iam_member" "deployer_tfstate" {
  count = var.create_wif && var.tfstate_bucket != "" ? 1 : 0

  bucket = var.tfstate_bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.deployer[0].email}"
}

# ── Workload Identity Federation for GitHub Actions ────────────────────────
resource "google_iam_workload_identity_pool" "github" {
  count = var.create_wif ? 1 : 0

  project                   = var.project_a_id
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions"

  depends_on = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count = var.create_wif ? 1 : 0

  project                            = var.project_a_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
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
  count = var.create_wif ? 1 : 0

  service_account_id = google_service_account.deployer[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository/${var.github_repo}"
}
