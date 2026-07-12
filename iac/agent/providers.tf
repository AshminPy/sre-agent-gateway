# This stack (iac/agent) deploys ALL agent-side resources into Project A.
# The GKE cluster and its cross-project IAM live in the separate iac/gke-access
# stack (Project B). Authentication uses Application Default Credentials:
#   gcloud auth application-default login
# or a Workload Identity Federation token in CI (see .github/workflows).

provider "google" {
  project = var.project_a_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_a_id
  region  = var.region
}
