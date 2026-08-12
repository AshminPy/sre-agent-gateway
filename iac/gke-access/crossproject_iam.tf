# Cross-project IAM: the minimal read-only access the agent needs in Project B.
#
# Least privilege — four read-only/tool predefined roles plus one narrow custom
# role (below) for the agent identity, and nothing broader. The agent (in
# Project A) uses these to investigate GKE incidents here via GKE Remote MCP.
# See docs/least-privilege-iam.md.

locals {
  # The four predefined cross-project roles the agent needs in Project B, and why:
  agent_crossproject_roles = [
    "roles/container.viewer",  # read-only GKE resources (pods, events, deployments) for RCA
    "roles/mcp.toolUser",      # invoke GKE Remote MCP tools/call
    "roles/logging.viewer",    # read GKE workload logs (live in Project B)
    "roles/monitoring.viewer", # read GKE metrics (live in Project B)
  ]
}

resource "google_project_iam_member" "agent_crossproject" {
  for_each = toset(local.agent_crossproject_roles)

  project = var.project_b_id
  role    = each.value
  member  = local.agent_principal_set
}

# issue #92: container.viewer does NOT include container.pods.getLogs -- confirmed
# live via `gcloud iam roles describe roles/container.viewer`, which lists only
# container.pods.get/getStatus/list. Without this, get_k8s_logs 403s on every
# incident (real failure, run_20260810_062832_bvoi). roles/container.developer does
# include container.pods.getLogs, but also grants broad write access (create/
# delete/update on pods, configmaps, and most other K8s resources -- confirmed via
# `gcloud iam roles describe roles/container.developer`), which violates least
# privilege for a read-only investigative agent. This custom role grants exactly
# the one missing permission and nothing else.
resource "google_project_iam_custom_role" "pod_log_reader" {
  project     = var.project_b_id
  role_id     = "podLogReader"
  title       = "GKE Pod Log Reader"
  description = "Read-only access to Kubernetes pod logs (container.pods.getLogs) -- the one permission roles/container.viewer does not include. See issue #92."
  permissions = ["container.pods.getLogs"]
  stage       = "GA"
}

resource "google_project_iam_member" "agent_pod_logs" {
  project = var.project_b_id
  role    = google_project_iam_custom_role.pod_log_reader.id
  member  = local.agent_principal_set
}

# The custom Cloud Run MCP fallback (if deployed in the agent stack) runs as its
# own SA and also needs read-only GKE access here.
resource "google_project_iam_member" "custom_mcp_crossproject" {
  count = var.custom_mcp_runtime_sa_email != "" ? 1 : 0

  project = var.project_b_id
  role    = "roles/container.viewer"
  member  = "serviceAccount:${var.custom_mcp_runtime_sa_email}"
}

resource "google_project_iam_member" "custom_mcp_pod_logs" {
  count = var.custom_mcp_runtime_sa_email != "" ? 1 : 0

  project = var.project_b_id
  role    = google_project_iam_custom_role.pod_log_reader.id
  member  = "serviceAccount:${var.custom_mcp_runtime_sa_email}"
}

# ── Deployer SA roles in Project B (only needed to manage THIS stack from CI) ─
# Deliberately tiny and B-scoped. Skip when applying locally with your own creds.
locals {
  deployer_b_roles = var.deployer_sa_email != "" ? [
    "roles/container.admin",                 # create/manage the demo GKE cluster
    "roles/compute.networkAdmin",            # the cluster's VPC/subnet
    "roles/serviceusage.serviceUsageAdmin",  # enable APIs in Project B
    "roles/resourcemanager.projectIamAdmin", # set the cross-project grants above
    # issue #92: resourcemanager.projectIamAdmin does NOT include iam.roles.create
    # (confirmed via `gcloud iam roles describe`) -- needed to manage the
    # pod_log_reader custom role above. roles/iam.roleAdmin is the narrowest
    # predefined role that grants it; scoped to Project B only, not org-level.
    "roles/iam.roleAdmin",
  ] : []
}

resource "google_project_iam_member" "deployer_b" {
  for_each = toset(local.deployer_b_roles)

  project = var.project_b_id
  role    = each.value
  member  = "serviceAccount:${var.deployer_sa_email}"
}
