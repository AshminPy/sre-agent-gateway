# IAP egress authorization for the agent identity.
#
# The Agent Gateway's default-deny egress checks the caller for the
# iap.webServiceVersions.egressViaIAP permission (granted by roles/iap.egressor)
# at the Agent Registry / IAP resource level — NOT project level. We grant it
# registry-wide to this agent's identity so it may egress to any destination
# registered in the Agent Registry (the endpoints registered post-apply by
# scripts/register_endpoints.py, plus the agent's own MCP server).
#
# This is the modern, declarative replacement for the codelab's out-of-band
# grant script. Gated on the gateway being enabled.

resource "google_iap_agent_registry_iam_member" "agent_egressor" {
  count    = var.enable_agent_gateway ? 1 : 0
  provider = google-beta

  project  = var.project_a_id
  location = var.region
  role     = "roles/iap.egressor"
  member   = local.agent_identity_member
}
