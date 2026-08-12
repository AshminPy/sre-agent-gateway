# Config-wiring test for issue #92's pod-log-read IAM gap.
#
# Runs against the REAL root module (same pattern as
# iac/agent/tests/agent_gateway_binding.tftest.hcl) -- google_project_iam_custom_role
# and google_project_iam_member aren't practical to fork into a standalone fixture,
# and this repo's own workflow already runs real `terraform plan`s against the live
# backend.
#
# `command = plan` only — never apply. Requires the same GCP auth as any other
# `terraform plan` in this stack (WIF in CI, ADC locally).
#
# Run with: terraform test (from iac/gke-access/)

variables {
  project_a_id = "sreagent-t2-demo"
  project_b_id = "sreagent-demo"
  region       = "us-central1"
}

# ── Custom role grants exactly container.pods.getLogs, nothing broader ─────────────
run "pod_log_reader_role_is_minimal" {
  command = plan

  assert {
    condition     = length(google_project_iam_custom_role.pod_log_reader.permissions) == 1
    error_message = "pod_log_reader must grant exactly one permission -- adding more would defeat the least-privilege point of a custom role instead of using a broader predefined role."
  }

  assert {
    condition     = contains(google_project_iam_custom_role.pod_log_reader.permissions, "container.pods.getLogs")
    error_message = "pod_log_reader must grant container.pods.getLogs -- the specific permission container.viewer is missing (issue #92)."
  }
}

# ── The agent identity is bound to the custom role ──────────────────────────────────
run "agent_is_bound_to_pod_log_reader" {
  command = plan

  assert {
    condition     = google_project_iam_member.agent_pod_logs.member == local.agent_principal_set
    error_message = "agent_pod_logs must bind the same agent_principal_set used by the other cross-project grants."
  }
}

# ── Custom-MCP fallback SA also gets pod-log read access when configured ───────────
run "custom_mcp_sa_gets_pod_log_reader_when_configured" {
  command = plan

  variables {
    custom_mcp_runtime_sa_email = "custom-mcp-runtime@sreagent-t2-demo.iam.gserviceaccount.com"
  }

  assert {
    condition     = length(google_project_iam_member.custom_mcp_pod_logs) == 1
    error_message = "custom_mcp_pod_logs must be created when custom_mcp_runtime_sa_email is set."
  }
}

# ── Custom-MCP fallback grant is skipped when no runtime SA is configured ──────────
run "custom_mcp_pod_log_reader_skipped_when_unconfigured" {
  command = plan

  assert {
    condition     = length(google_project_iam_member.custom_mcp_pod_logs) == 0
    error_message = "custom_mcp_pod_logs must not be created when custom_mcp_runtime_sa_email is empty (default)."
  }
}
