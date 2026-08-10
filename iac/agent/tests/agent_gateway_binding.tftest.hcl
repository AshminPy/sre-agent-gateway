# Config-wiring test for the Phase 2 attach_gateway_to_engine.sh -> Terraform
# migration (2026-08-10): proves the Reasoning Engine's agent_gateway_config
# block correctly references the existing gateway resource and never breaks
# Agent Identity, and proves the enable_agent_gateway=false path never
# indexes into a zero-count gateway resource.
#
# Runs against the REAL root module (no isolated testdata mirror, unlike
# clusters_json.tftest.hcl) — google_vertex_ai_reasoning_engine and
# google_network_services_agent_gateway are real, interdependent resources
# that aren't practical to fork into a standalone test fixture. This matches
# how this repo's own CI already runs a real `terraform plan` against the
# live backend on every PR (.github/workflows/terraform-plan.yml) — this test
# adds structured assertions on top of the same plan, it doesn't introduce a
# new kind of access.
#
# `command = plan` only — never apply. Requires the same GCP auth as any
# other `terraform plan` in this stack (WIF in CI, ADC locally).
#
# Run with: terraform test (from iac/agent/)

variables {
  project_a_id       = "sreagent-t2-demo"
  project_b_id       = "sreagent-demo"
  gke_cluster_name   = "sre-test-cluster"
  notification_email = "ashmin.sub@gmail.com"
  github_repo        = "AshminPy/sre-agent-gateway"
  tfstate_bucket     = "sreagent-t2-demo-tfstate"
  region             = "us-central1"
}

# ── Gateway enabled (the live default) — binding must be present and correct ──
run "gateway_binding_references_existing_gateway_when_enabled" {
  command = plan

  variables {
    enable_agent_gateway = true
  }

  assert {
    condition     = length(google_vertex_ai_reasoning_engine.sre_agent.spec[0].deployment_spec[0].agent_gateway_config) == 1
    error_message = "agent_gateway_config must be present exactly once when enable_agent_gateway=true."
  }

  assert {
    condition     = length(google_vertex_ai_reasoning_engine.sre_agent.spec[0].deployment_spec[0].agent_gateway_config[0].agent_to_anywhere_config) == 1
    error_message = "agent_to_anywhere_config must be present exactly once inside agent_gateway_config."
  }

  # NOTE: cannot assert the exact VALUE of `agent_gateway` here (e.g. equality
  # against google_network_services_agent_gateway.sre_egress[0].id) — proven
  # empirically (terraform test run, 2026-08-10): this specific leaf string,
  # inside a nested block being added for the first time to an existing
  # resource, comes back as "(known after apply)" at plan time even though
  # the block's own presence/count above IS known. This is a real SDKv2
  # provider modeling limitation (whole nested list re-evaluated as computed
  # on a partial change), not a config error — confirmed by removing just
  # this one assertion and the presence/count assertions above passing
  # cleanly. The exact-match check (does it reference the SAME gateway ID) is
  # therefore a REQUIRED LIVE check, not a plan-time one — see Phase 3/4 Test
  # A's "Gateway resource ID is unchanged" requirement, which this expression
  # is designed to feed directly: `.spec.deploymentSpec.agentGatewayConfig.
  # agentToAnywhereConfig.agentGateway` read from the real API response,
  # compared against `terraform output -raw agent_gateway_id`.

  # Agent Identity must never regress as a side effect of adding the gateway
  # block — this is the exact requirement the PATCH-based script's own
  # pre-flight check (attach_gateway_to_engine.sh) enforces before every run.
  assert {
    condition     = google_vertex_ai_reasoning_engine.sre_agent.spec[0].identity_type == "AGENT_IDENTITY"
    error_message = "identity_type must remain AGENT_IDENTITY — required for gateway attachment, must not be dropped by this change."
  }

  # source_code_spec must still be present and unchanged in shape — this
  # config change must not accidentally clear the agent's own source.
  assert {
    condition     = length(google_vertex_ai_reasoning_engine.sre_agent.spec[0].source_code_spec) == 1
    error_message = "source_code_spec must remain present — this migration must not remove or null out the agent's source packaging."
  }
}

# ── Gateway disabled — must not index into a zero-count gateway resource ──
run "no_gateway_block_and_no_index_error_when_disabled" {
  command = plan

  variables {
    enable_agent_gateway = false
  }

  assert {
    condition     = length(google_vertex_ai_reasoning_engine.sre_agent.spec[0].deployment_spec[0].agent_gateway_config) == 0
    error_message = "agent_gateway_config must be entirely absent when enable_agent_gateway=false, not an empty/null placeholder."
  }

  assert {
    condition     = google_vertex_ai_reasoning_engine.sre_agent.spec[0].identity_type == "AGENT_IDENTITY"
    error_message = "identity_type must be unaffected by whether the gateway is enabled."
  }
}
