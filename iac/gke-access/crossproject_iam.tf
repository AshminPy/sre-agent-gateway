# Cross-project IAM: the minimal read-only access the agent needs in Project B.
#
# Least privilege — exactly four read-only/tool roles for the agent identity, and
# nothing broader. The agent (in Project A) uses these to investigate GKE
# incidents here via GKE Remote MCP. See docs/least-privilege-iam.md.
#
# Pod-log read access (container.pods.getLogs / K8s "pods/log" resource) is
# deliberately NOT granted here via a Cloud IAM custom role. GKE's authorizer
# accepts EITHER a Cloud IAM permission OR a native K8s RBAC grant for that
# specific resource -- this cluster already uses K8s-native RBAC for it
# (k8s/rbac.yaml, issue #92), so a parallel Cloud IAM custom role would be
# duplicate infrastructure for no benefit. See k8s/rbac.yaml's header comment.

locals {
  # The four cross-project roles the agent needs in Project B, and why:
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

# The custom Cloud Run MCP fallback (if deployed in the agent stack) runs as its
# own SA and also needs read-only GKE access here.
resource "google_project_iam_member" "custom_mcp_crossproject" {
  count = var.custom_mcp_runtime_sa_email != "" ? 1 : 0

  project = var.project_b_id
  role    = "roles/container.viewer"
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
  ] : []
}

resource "google_project_iam_member" "deployer_b" {
  for_each = toset(local.deployer_b_roles)

  project = var.project_b_id
  role    = each.value
  member  = "serviceAccount:${var.deployer_sa_email}"
}
