# Config-wiring + validation test for issue #63's token-usage warning slice, corrected
# 2026-08-12 after review caught real bugs in the first version (wrong metric filter
# double-counting per-node token events, wrong alert resource.type, no disable-on-zero
# handling, no validation on max_tokens_per_run itself).
#
# Runs against the REAL root module (same pattern as agent_gateway_binding.tftest.hcl)
# -- google_monitoring_alert_policy and google_logging_metric aren't practical to fork
# into a standalone fixture, and this repo's own CI already runs a real `terraform plan`
# against the live backend on every PR.
#
# `command = plan` only — never apply. Requires the same GCP auth as any other
# `terraform plan` in this stack (WIF in CI, ADC locally).
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

# ── Default threshold: 100000 * 0.8 = 80000 ─────────────────────────────────────────
run "default_threshold_is_80000" {
  command = plan

  assert {
    condition     = google_monitoring_alert_policy.token_usage_warning.conditions[0].condition_threshold[0].threshold_value == 80000
    error_message = "Default max_tokens_per_run=100000 * token_warning_ratio=0.8 must produce threshold_value=80000."
  }
}

# ── Custom limit + ratio produce the correct threshold ──────────────────────────────
run "custom_limit_and_ratio_produce_correct_threshold" {
  command = plan

  variables {
    max_tokens_per_run  = 50000
    token_warning_ratio = 0.6
  }

  assert {
    condition     = google_monitoring_alert_policy.token_usage_warning.conditions[0].condition_threshold[0].threshold_value == 30000
    error_message = "max_tokens_per_run=50000 * token_warning_ratio=0.6 must produce threshold_value=30000."
  }
}

# ── MAX_TOKENS_PER_RUN reaches Agent Engine ──────────────────────────────────────────
run "max_tokens_per_run_reaches_agent_engine_env" {
  command = plan

  variables {
    max_tokens_per_run = 75000
  }

  assert {
    condition = anytrue([
      for e in google_vertex_ai_reasoning_engine.sre_agent.spec[0].deployment_spec[0].env :
      e.name == "MAX_TOKENS_PER_RUN" && e.value == "75000"
    ])
    error_message = "MAX_TOKENS_PER_RUN env var on the Reasoning Engine must equal var.max_tokens_per_run as a string."
  }
}

# ── Metric filters only the final sre_agent_run event, not every tokens_total log ───
run "metric_filters_only_sre_agent_run_event_type" {
  command = plan

  assert {
    condition     = strcontains(google_logging_metric.token_usage.filter, "jsonPayload.event_type=\"sre_agent_run\"")
    error_message = "token_usage metric filter must restrict to event_type=\"sre_agent_run\" -- agent/nodes/*.py's node_token_usage events also carry tokens_total and would otherwise be double-counted."
  }

  assert {
    condition     = strcontains(google_logging_metric.token_usage.filter, "jsonPayload.tokens_total > 0")
    error_message = "token_usage metric filter must still require tokens_total > 0."
  }
}

# ── Alert uses the real ReasoningEngine resource type, confirmed via live log data ──
run "alert_uses_reasoning_engine_resource_type" {
  command = plan

  assert {
    condition     = strcontains(google_monitoring_alert_policy.token_usage_warning.conditions[0].condition_threshold[0].filter, "resource.type=\"aiplatform.googleapis.com/ReasoningEngine\"")
    error_message = "token_usage_warning's filter must use resource.type=\"aiplatform.googleapis.com/ReasoningEngine\", confirmed to match this project's real sre_agent_run log entries -- not \"global\"."
  }
}

# ── max_tokens_per_run=0 disables the warning alert, not just the hard cap ──────────
run "zero_max_tokens_disables_the_warning_alert" {
  command = plan

  variables {
    max_tokens_per_run = 0
  }

  assert {
    condition     = google_monitoring_alert_policy.token_usage_warning.enabled == false
    error_message = "max_tokens_per_run=0 must disable the token_usage_warning alert, not leave it enabled with a meaningless threshold of 0."
  }
}

# ── Negative or decimal limits are rejected by the variable's own validation ────────
run "negative_max_tokens_is_rejected" {
  command = plan

  variables {
    max_tokens_per_run = -1
  }

  expect_failures = [
    var.max_tokens_per_run,
  ]
}

run "decimal_max_tokens_is_rejected" {
  command = plan

  variables {
    max_tokens_per_run = 100.5
  }

  expect_failures = [
    var.max_tokens_per_run,
  ]
}
