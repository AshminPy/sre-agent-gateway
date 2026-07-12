# This stack deploys the GKE cluster (optional) and the cross-project IAM that
# lets the agent (in Project A) read GKE resources in Project B. All resources
# here target Project B.

provider "google" {
  project = var.project_b_id
  region  = var.region
}
