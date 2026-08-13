# Config-wiring test for issue #76: OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT
# must stay disabled on the deployed Reasoning Engine. This flag included real
# prompt/response text (built from k8s pod logs/events) in Cloud Trace spans at 100%
# sampling, live and undocumented since 2026-07-13 -- disabled deliberately after
# review. See docs/trace-content-capture.md for the full decision record.
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

run "genai_content_capture_is_disabled_on_the_reasoning_engine" {
  command = plan

  assert {
    condition = anytrue([
      for e in google_vertex_ai_reasoning_engine.sre_agent.spec[0].deployment_spec[0].env :
      e.name == "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT" && e.value == "false"
    ])
    error_message = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT must be \"false\" -- real prompt/response content (built from k8s evidence) must never be captured into Cloud Trace. See issue #76."
  }
}

run "trace_sampling_rate_is_unaffected_by_the_content_capture_fix" {
  command = plan

  assert {
    condition = anytrue([
      for e in google_vertex_ai_reasoning_engine.sre_agent.spec[0].deployment_spec[0].env :
      e.name == "OTEL_TRACES_SAMPLER_ARG" && e.value == "1.0"
    ])
    error_message = "OTEL_TRACES_SAMPLER_ARG must stay at 1.0 -- sampling rate is a separate concern from content capture; normal performance tracing must not regress as a side effect of the #76 fix."
  }
}
