# Phase 1: on-prem / non-GKE cluster access via GKE Fleet Connect Gateway.
#
# REVISED 2026-09-05 — separated three concerns that were previously bundled
# into one Terraform resource with a kubeconfig-dependent local-exec
# provisioner. That provisioner ran `gcloud ... register` / `generate-gateway-rbac`
# against `$HOME/.kube/config` on whatever machine ran `terraform apply` —
# harmless from a trusted operator's laptop, but a real landmine the moment
# CI ran it: GitHub Actions has no legitimate cluster-admin kubeconfig, so
# every CI apply silently tore this down (real regression, found and fixed
# this date — see PHASE1_EVIDENCE_LOG.md).
#
# The three concerns, now separated to match the same operational boundary
# used at work (infra vs. Kubernetes RBAC are different lifecycles, owned
# and reviewed independently):
#
#   1. Google IAM (WHO may call Connect Gateway at all) — stays here, in
#      Terraform. Plain google_project_iam_member, no kubeconfig involved.
#   2. Fleet membership registration + Connect Agent install — an explicit,
#      one-time (or rare) onboarding action performed by an authorized
#      operator with their own temporary kubeconfig access. NOT a Terraform
#      resource, NOT run by CI. See docs/connect-gateway-onprem.md.
#   3. Kubernetes RBAC (WHAT that identity can do once inside the cluster)
#      — moved entirely to the separate AshminPy/sre-k8s-rbac repo. Applied
#      by an authorized operator via plain `kubectl apply`, never by this
#      Terraform, never by CI here.
#
# Set var.onprem_fleet_membership to activate item 1 below. Empty (default)
# = this resource doesn't exist, matching every environment without an
# on-prem cluster registered.

resource "google_project_iam_member" "mcp_runtime_gateway_reader" {
  count = var.enable_custom_mcp && var.onprem_fleet_membership != "" ? 1 : 0

  project = var.project_a_id
  role    = "roles/gkehub.gatewayReader" # read-only: gateway.generateCredentials, gateway.get, memberships.get — no gatewayAdmin/Editor
  member  = "serviceAccount:${google_service_account.mcp_runtime[0].email}"
}
