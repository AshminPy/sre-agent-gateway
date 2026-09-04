# Custom MCP Agent Registry registration (issue #33) -- kept in its own file, and its
# own resource, because it differs from the static Google-API registrations in
# agent_registry.tf in two ways that matter operationally:
#
#   1. Dynamic content. mcp_server_spec.content is the real MCP tools/list output of
#      mcp/server.py (27 tools, ~7.3KB), so it changes whenever mcp/ changes. It is
#      written to mcp/tool_spec.json by scripts/build_mcp_tool_spec.py -- the same
#      "generate an artifact, then read it with a Terraform function" pattern this
#      stack already uses for agent.tar.gz + filebase64() in agent_engine.tf.
#      Terraform cannot introspect a Python MCP server itself.
#
#   2. Health-gated ordering. This resource's content must not change to describe
#      new tools until the Cloud Run revision that actually serves them has passed
#      verification -- otherwise the registry and the running server can disagree
#      about what the MCP server supports, a real runtime/evidence-integrity risk.
#      terraform-apply.yml enforces the ordering, not this file: mcp/tool_spec.json
#      is pinned to whatever is CURRENTLY LIVE before the first two applies (Cloud
#      Run's own update included) run, and is only regenerated to match this
#      commit's real mcp/ code after "Verify MCP Cloud Run revision is healthy"
#      passes -- immediately followed by a third, untargeted apply that is the only
#      one where this resource's content can actually change. depends_on below
#      still encodes the Cloud Run dependency for Terraform's own graph; it is the
#      CI step order plus the content-pinning above that makes the health gate real
#      (Terraform has no native way to await an external, imperative health check).
#
# The URL comes straight from the Cloud Run resource rather than a CI-side
# `gcloud run services describe` lookup, so it can no longer drift from what is
# actually deployed.
#
# count/[0] mirrors google_cloud_run_v2_service.mcp's own var.enable_custom_mcp gating --
# with the feature off there is no Cloud Run service to register.
resource "google_agent_registry_service" "custom_mcp" {
  provider = google-beta
  count    = var.enable_custom_mcp ? 1 : 0

  project      = var.project_a_id
  location     = var.region
  service_id   = "sre-k8s-mcp"
  display_name = "Custom SRE Kubernetes MCP"
  # Matches the live registration verbatim. The retired script passed a slightly
  # different string (" - fallback" vs ", fallback") but only on CREATE, never on
  # update -- so the live value is what the service was first registered with, and
  # this keeps the import diff-free rather than rewording a live resource for cosmetics.
  description = "Read-only custom Kubernetes MCP server (Cloud Run), fallback to GKE Remote MCP, on-prem/non-GKE clusters via Connect Gateway"

  mcp_server_spec {
    type    = "TOOL_SPEC"
    content = file("${path.module}/../../mcp/tool_spec.json")
  }

  interfaces {
    protocol_binding = "JSONRPC"
    url              = google_cloud_run_v2_service.mcp[0].uri
  }

  # Readiness gate, native to Terraform's graph (precondition works on 1.4.7 -- no
  # -target apply, no provisioner). Refuses to publish a registration when Cloud Run
  # has no ready revision at all. This is deliberately NOT presented as a full
  # replacement for CI's "Verify MCP Cloud Run revision is healthy" step, which still
  # runs and still fails the job: latest_ready_revision reports the last revision that
  # became ready, so on a push that both changes mcp/ AND ships a revision that never
  # starts, this attribute can still hold the previous good revision. See that CI
  # step's own comment for the failure mode it was written to catch.
  lifecycle {
    precondition {
      condition     = google_cloud_run_v2_service.mcp[0].latest_ready_revision != ""
      error_message = "Custom MCP Cloud Run service has no ready revision -- refusing to register it in the Agent Registry."
    }
  }

  depends_on = [google_cloud_run_v2_service.mcp]
}
