# Config-wiring test for issue #75's completion-event double-counting fix.
#
# Two separate completion-event emitters exist for one investigation:
# agent/main.py's obs_event (event_type="sre_agent_run", stdout, the canonical
# metrics-source event) and agent/nodes/rca_builder.py's separate, richer
# "sre-agent-investigations" Cloud Logging entry (SRE-review audit record, no
# event_type field at all). Both share overlapping field names (run_id, status,
# confidence_band, estimated_cost_usd, cluster_routing_method, loop_exit_reason,
# total_latency_s) -- confirmed by reading both entries directly. Every metric
# below that reads one of those fields must restrict to event_type="sre_agent_run"
# so it counts each investigation exactly once, same fix already applied to
# token_usage (issue #63 PR 1).
#
# Runs against the REAL root module (same pattern as token_usage_monitoring.tftest.hcl).
# `command = plan` only — never apply.
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

run "invocations_metric_restricted_to_sre_agent_run_event" {
  command = plan
  assert {
    condition     = strcontains(google_logging_metric.invocations.filter, "jsonPayload.event_type=\"sre_agent_run\"")
    error_message = "invocations metric must restrict to event_type=\"sre_agent_run\" -- rca_builder.py's separate completion event also has a run_id field and would otherwise be double-counted."
  }
}

run "errors_metric_restricted_to_sre_agent_run_event" {
  command = plan
  assert {
    condition     = strcontains(google_logging_metric.errors.filter, "jsonPayload.event_type=\"sre_agent_run\"")
    error_message = "errors metric must restrict to event_type=\"sre_agent_run\"."
  }
}

run "escalations_metric_restricted_to_sre_agent_run_event" {
  command = plan
  assert {
    condition     = strcontains(google_logging_metric.escalations.filter, "jsonPayload.event_type=\"sre_agent_run\"")
    error_message = "escalations metric must restrict to event_type=\"sre_agent_run\"."
  }
}

run "investigation_cost_metric_restricted_to_sre_agent_run_event" {
  command = plan
  assert {
    condition     = strcontains(google_logging_metric.investigation_cost.filter, "jsonPayload.event_type=\"sre_agent_run\"")
    error_message = "investigation_cost metric must restrict to event_type=\"sre_agent_run\"."
  }
}

run "confidence_band_metric_restricted_to_sre_agent_run_event" {
  command = plan
  assert {
    condition     = strcontains(google_logging_metric.confidence_band.filter, "jsonPayload.event_type=\"sre_agent_run\"")
    error_message = "confidence_band metric must restrict to event_type=\"sre_agent_run\"."
  }
}

run "loop_exit_reason_metric_restricted_to_sre_agent_run_event" {
  command = plan
  assert {
    condition     = strcontains(google_logging_metric.loop_exit_reason.filter, "jsonPayload.event_type=\"sre_agent_run\"")
    error_message = "loop_exit_reason metric must restrict to event_type=\"sre_agent_run\"."
  }
}

run "unresolved_cluster_metric_restricted_to_sre_agent_run_event" {
  command = plan
  assert {
    condition     = strcontains(google_logging_metric.unresolved_cluster.filter, "jsonPayload.event_type=\"sre_agent_run\"")
    error_message = "unresolved_cluster metric must restrict to event_type=\"sre_agent_run\"."
  }
}

run "investigation_latency_metric_restricted_to_sre_agent_run_event" {
  command = plan
  assert {
    condition     = strcontains(google_logging_metric.investigation_latency.filter, "jsonPayload.event_type=\"sre_agent_run\"")
    error_message = "investigation_latency metric must restrict to event_type=\"sre_agent_run\"."
  }
}

# ── logName-based metrics are a separate, unaffected logger -- must NOT gain this filter ──
run "tool_failures_metric_unaffected_uses_logname_not_jsonpayload" {
  command = plan
  assert {
    condition     = strcontains(google_logging_metric.tool_failures.filter, "logName=")
    error_message = "tool_failures metric filters on a completely separate logger (sre-agent-tool-failures) -- this fix must not touch it."
  }
}
