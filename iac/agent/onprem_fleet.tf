# Phase 1: on-prem / non-GKE cluster access via GKE Fleet Connect Gateway.
#
# Google's fleet membership registration for a non-GKE (kind/k3s/on-prem)
# cluster is NOT fully Terraform-native: `gcloud container fleet memberships
# register --context=...` both creates the Hub-side membership object AND
# installs the Connect Agent workload onto the target cluster in one command
# (verified against docs.cloud.google.com/kubernetes-engine/fleet-management —
# "Connect agent will always be installed" for third-party cluster
# registration). There is no equivalent single Terraform resource: an
# in-isolation `google_gke_hub_membership` only manages the Hub-API object,
# not the in-cluster agent install, and mixing the two management planes for
# the same membership ID is unverified. The smallest safe design is to keep
# `gcloud` as the actual mechanism, orchestrated by Terraform (idempotent,
# triggers on the inputs below, torn down on destroy) — the same
# generate-before-plan / pin-to-live pattern already used elsewhere in this
# repo (see scripts/build_mcp_tool_spec.py's header comment) for a capability
# the provider doesn't expose declaratively.
#
# Read-only RBAC (clusterrole/view) is granted to the custom MCP's OWN runtime
# SA (google_service_account.mcp_runtime), not the AGENT_IDENTITY principal —
# Connect Gateway's documented impersonation model uses plain SA/user
# identities, a different WIF pool family than Agent Identity's
# agents.global.org-*.system.id.goog pool, and mixing the two is unverified.
#
# Set var.onprem_fleet_membership to activate. Empty (default) = these
# resources don't exist, matching every other environment that doesn't have
# an on-prem cluster to register.

resource "google_project_iam_member" "mcp_runtime_gateway_reader" {
  count = var.enable_custom_mcp && var.onprem_fleet_membership != "" ? 1 : 0

  project = var.project_a_id
  role    = "roles/gkehub.gatewayReader" # read-only: gateway.generateCredentials, gateway.get, memberships.get — no gatewayAdmin/Editor
  member  = "serviceAccount:${google_service_account.mcp_runtime[0].email}"
}

resource "terraform_data" "onprem_fleet_registration" {
  count = var.enable_custom_mcp && var.onprem_fleet_membership != "" ? 1 : 0

  triggers_replace = {
    project          = var.project_a_id
    membership       = var.onprem_fleet_membership
    kubeconfig_ctx   = var.onprem_fleet_kubeconfig_context
    runtime_sa_email = google_service_account.mcp_runtime[0].email
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail
      STATE=$(gcloud container fleet memberships describe "${self.triggers_replace.membership}" \
        --project="${self.triggers_replace.project}" --format="value(state.code)" 2>/dev/null || echo "MISSING")
      if [ "$STATE" != "READY" ]; then
        gcloud container fleet memberships register "${self.triggers_replace.membership}" \
          --project="${self.triggers_replace.project}" \
          --context="${self.triggers_replace.kubeconfig_ctx}" \
          --kubeconfig="$HOME/.kube/config" \
          --enable-workload-identity \
          --has-private-issuer
      fi
      gcloud container fleet memberships generate-gateway-rbac \
        --project="${self.triggers_replace.project}" \
        --membership="${self.triggers_replace.membership}" \
        --users="${self.triggers_replace.runtime_sa_email}" \
        --role=clusterrole/view \
        --context="${self.triggers_replace.kubeconfig_ctx}" \
        --kubeconfig="$HOME/.kube/config" \
        --apply
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      set -euo pipefail
      gcloud container fleet memberships generate-gateway-rbac \
        --project="${self.triggers_replace.project}" \
        --membership="${self.triggers_replace.membership}" \
        --users="${self.triggers_replace.runtime_sa_email}" \
        --role=clusterrole/view \
        --context="${self.triggers_replace.kubeconfig_ctx}" \
        --kubeconfig="$HOME/.kube/config" \
        --revoke || true
      gcloud container fleet memberships unregister "${self.triggers_replace.membership}" \
        --project="${self.triggers_replace.project}" \
        --context="${self.triggers_replace.kubeconfig_ctx}" \
        --kubeconfig="$HOME/.kube/config" || true
    EOT
  }
}
