# Observability: Log Analytics, log-based metrics derived from the agent's
# structured run logs, a notification channel, and alert policies.

# Log Analytics on the _Default bucket — required for the Agent Gateway console
# observability tabs to load.
resource "google_logging_project_bucket_config" "default_analytics" {
  project          = var.project_a_id
  location         = "global"
  bucket_id        = "_Default"
  retention_days   = var.log_analytics_retention_days
  enable_analytics = true

  depends_on = [google_project_service.apis]
}

# ── Log-based metrics (from jsonPayload the agent emits per run) ────────────
#
# issue #75: two separate completion-event emitters exist for one investigation --
# agent/main.py's obs_event (event_type="sre_agent_run", stdout, the canonical
# metrics-source event per its own PRODUCTION-LAUNCH-PLAN.md Priority 10 comment)
# and agent/nodes/rca_builder.py's separate, richer "sre-agent-investigations"
# Cloud Logging entry (SRE-review audit record -- validation_status/sre_feedback/etc,
# not meant to double as the metrics source). Both share overlapping field names
# (run_id, status, confidence_band, estimated_cost_usd, cluster_routing_method,
# loop_exit_reason, total_latency_s) -- confirmed by reading both entries directly,
# not assumed -- but only main.py's event carries event_type="sre_agent_run";
# rca_builder.py's entry has no such field at all. Every filter below that reads
# one of those overlapping fields is restricted to event_type="sre_agent_run" so
# it counts each investigation exactly once, matching the same fix already applied
# to token_usage below (issue #63 PR 1).

resource "google_logging_metric" "invocations" {
  name    = "sre_agent/invocations"
  project = var.project_a_id
  filter  = "jsonPayload.event_type=\"sre_agent_run\" AND jsonPayload.run_id:*"
  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    display_name = "SRE Agent Invocations"
  }
  depends_on = [google_project_service.apis]
}

resource "google_logging_metric" "errors" {
  name    = "sre_agent/errors"
  project = var.project_a_id
  filter  = "jsonPayload.event_type=\"sre_agent_run\" AND jsonPayload.status=\"error\""
  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    display_name = "SRE Agent Errors"
  }
  depends_on = [google_project_service.apis]
}

resource "google_logging_metric" "escalations" {
  name    = "sre_agent/escalations"
  project = var.project_a_id
  filter  = "jsonPayload.event_type=\"sre_agent_run\" AND jsonPayload.confidence_band=\"escalate\""
  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    display_name = "SRE Agent Escalation Events"
  }
  depends_on = [google_project_service.apis]
}

resource "google_logging_metric" "investigation_cost" {
  name            = "sre_agent/investigation_cost_usd"
  project         = var.project_a_id
  filter          = "jsonPayload.event_type=\"sre_agent_run\" AND jsonPayload.estimated_cost_usd > 0"
  value_extractor = "EXTRACT(jsonPayload.estimated_cost_usd)"
  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "DISTRIBUTION"
    display_name = "SRE Agent Investigation Cost (USD)"
    unit         = "USD"
  }
  bucket_options {
    explicit_buckets {
      bounds = [0.01, 0.02, 0.05, 0.10, 0.20, 0.50, 1.00]
    }
  }
  depends_on = [google_project_service.apis]
}

# Provider-neutral token-usage warning (issue #63) — jsonPayload.tokens_total is real,
# accurate telemetry agent/llm/'s adapter design produces for any provider (not a
# Gemini-specific dollar figure). Deliberately separate from investigation_cost/cost_spike
# above, which this change does not touch or remove.
#
# Filter is restricted to event_type="sre_agent_run" specifically (agent/main.py's one
# final observability event per run) -- agent/nodes/*.py's node_token_usage events ALSO
# carry a non-zero tokens_total (the running cumulative total after that node's call),
# so an unrestricted `tokens_total > 0` filter would record 3+ data points per single
# investigation instead of 1, most of them partial/intermediate values, not the true
# final total. Confirmed with real log data from run_20260812_101219_mbwh: 1
# sre_agent_run event (tokens_total=1624) + 2 node_token_usage events
# (tokens_total=1366, 258) all matched the old unrestricted filter.
resource "google_logging_metric" "token_usage" {
  name            = "sre_agent/investigation_tokens_total"
  project         = var.project_a_id
  filter          = "jsonPayload.event_type=\"sre_agent_run\" AND jsonPayload.tokens_total > 0"
  value_extractor = "EXTRACT(jsonPayload.tokens_total)"
  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "DISTRIBUTION"
    display_name = "SRE Agent Investigation Tokens (total)"
  }
  bucket_options {
    explicit_buckets {
      bounds = [1000, 5000, 10000, 25000, 50000, 75000, 100000, 150000]
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_logging_metric" "confidence_band" {
  name             = "sre_agent/confidence_band"
  project          = var.project_a_id
  filter           = "jsonPayload.event_type=\"sre_agent_run\" AND jsonPayload.confidence_band != \"\""
  label_extractors = { "band" = "EXTRACT(jsonPayload.confidence_band)" }
  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    display_name = "SRE Agent Confidence Band"
    labels {
      key         = "band"
      value_type  = "STRING"
      description = "auto | review | escalate"
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_logging_metric" "loop_exit_reason" {
  name             = "sre_agent/loop_exit_reason"
  project          = var.project_a_id
  filter           = "jsonPayload.event_type=\"sre_agent_run\" AND jsonPayload.loop_exit_reason != \"\""
  label_extractors = { "reason" = "EXTRACT(jsonPayload.loop_exit_reason)" }
  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    display_name = "SRE Agent Loop Exit Reason"
    labels {
      key         = "reason"
      value_type  = "STRING"
      description = "enough_evidence | max_steps | timed_out | oscillating | stuck | zero_new_facts"
    }
  }
  depends_on = [google_project_service.apis]
}

# One log entry per failed tool call (emitted by nodes/tool_executor.py).
# Broken down by tool name, cluster, and MCP source — use this to find which
# tools fail most, on which clusters, and via which MCP source (gke_remote_mcp
# vs k8s_mcp — the "mcp_source" label below is what the GKE-MCP-failures and
# custom-MCP-failures alert policies filter on).
resource "google_logging_metric" "tool_failures" {
  name    = "sre_agent/tool_failures"
  project = var.project_a_id
  filter  = "logName=\"projects/${var.project_a_id}/logs/sre-agent-tool-failures\""

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    display_name = "SRE Agent Tool Failures"

    labels {
      key         = "tool"
      value_type  = "STRING"
      description = "MCP tool name that failed"
    }
    labels {
      key         = "cluster"
      value_type  = "STRING"
      description = "Target cluster where the tool was called"
    }
    labels {
      key         = "mcp_source"
      value_type  = "STRING"
      description = "gke_remote_mcp | k8s_mcp — which MCP source the failing call went to"
    }
  }

  label_extractors = {
    "tool"       = "EXTRACT(jsonPayload.tool)"
    "cluster"    = "EXTRACT(jsonPayload.cluster)"
    "mcp_source" = "EXTRACT(jsonPayload.mcp_source)"
  }

  depends_on = [google_project_service.apis]
}

# One log entry per mcp_router-level routing safe-stop (emitted by
# nodes/mcp_router.py:_log_routing_failure — the cluster was already resolved
# by context_resolver but has no usable/enabled MCP registry entry by the
# time mcp_router runs). See PRODUCTION-LAUNCH-PLAN.md Priority 10 ("routing
# failures"). Distinct from the unresolved_cluster metric below, which covers
# the earlier context_resolver stage (no cluster identified at all).
resource "google_logging_metric" "routing_failures" {
  name    = "sre_agent/routing_failures"
  project = var.project_a_id
  filter  = "logName=\"projects/${var.project_a_id}/logs/sre-agent-routing-failures\""

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    display_name = "SRE Agent Routing Failures"

    labels {
      key         = "cluster"
      value_type  = "STRING"
      description = "Cluster mcp_router could not route to"
    }
  }

  label_extractors = {
    "cluster" = "EXTRACT(jsonPayload.cluster)"
  }

  depends_on = [google_project_service.apis]
}

# Unknown/ambiguous cluster — context_resolver.py's deterministic routing
# safe-stop (agent/mcp_client.py:resolve_cluster_routing, priority-chain step
# 5 "human safe-stop") could not identify any cluster at all, so the
# investigation stopped instead of guessing. Read from the same per-run
# structured log as the other RCA-derived metrics above — cluster_routing_method
# is set on every run, "unresolved" only on this safe-stop path.
resource "google_logging_metric" "unresolved_cluster" {
  name    = "sre_agent/unresolved_cluster"
  project = var.project_a_id
  filter  = "jsonPayload.event_type=\"sre_agent_run\" AND jsonPayload.cluster_routing_method=\"unresolved\""

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    display_name = "SRE Agent Unresolved/Ambiguous Cluster Safe-Stops"
  }

  depends_on = [google_project_service.apis]
}

# One log entry per permanent evidence-storage (GCS) write failure (both
# retry attempts exhausted — emitted by gcs_client.py:_log_evidence_storage_failure).
# A failure here means the audit chain is broken for that evidence item — see
# PRODUCTION-LAUNCH-PLAN.md Priority 10 ("evidence-storage success/failure").
resource "google_logging_metric" "evidence_storage_failures" {
  name    = "sre_agent/evidence_storage_failures"
  project = var.project_a_id
  filter  = "logName=\"projects/${var.project_a_id}/logs/sre-agent-evidence-storage-failures\""

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    display_name = "SRE Agent Evidence Storage Failures"
  }

  depends_on = [google_project_service.apis]
}

# One log entry per response-guard sanitize-call error (mcp/response_guard.py's
# on_call_tool -- Model Armor itself unreachable/erroring on a tool RESPONSE
# check). The guard fails OPEN on this event (delivers the real tool result) --
# this metric/alert is the "loud alerting" half of that decision, not a
# duplicate of it. Filter is textPayload, not jsonPayload/severity, because
# mcp/server.py's plain `logging.basicConfig` output lands in Cloud Run's
# default stdout/stderr capture with severity always DEFAULT (verified live,
# 2026-09-06: existing WARNING/ERROR-level lines from this same logger show an
# empty severity field) -- a severity-based filter would never match.
resource "google_logging_metric" "mcp_model_armor_fail_open" {
  name    = "sre_agent/mcp_model_armor_fail_open"
  project = var.project_a_id
  filter  = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"sre-k8s-mcp\" AND textPayload:\"model_armor_fail_open\""

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    display_name = "SRE Agent Custom MCP Model Armor Fail-Open"
  }

  depends_on = [google_project_service.apis]
}

# Section 8 (2026-09-08): a SEPARATE, distinct failure mode from the per-call
# fail-open metric above. mcp/response_guard.py's Model Armor client can fail
# to INITIALIZE at all (bad MODEL_ARMOR_RESPONSE_TEMPLATE, IAM misconfig, a
# transient construction error) -- when that happens, self._client stays None
# for the process/revision's entire lifetime and EVERY subsequent tool
# response goes out unsanitized, permanently, until the next
# redeploy/restart. This used to log a plain WARNING with no alertable
# signal at all; response_guard.py now logs ERROR with this exact string.
resource "google_logging_metric" "mcp_model_armor_guard_init_failed" {
  name    = "sre_agent/mcp_model_armor_guard_init_failed"
  project = var.project_a_id
  filter  = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"sre-k8s-mcp\" AND textPayload:\"model_armor_guard_init_failed\""

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    display_name = "SRE Agent Custom MCP Model Armor Guard Init Failure"
  }

  depends_on = [google_project_service.apis]
}

# Section 11 (2026-09-08): closes a real gap the Section 5/6 dynamic Connect
# Gateway migration introduced without a matching alert -- the generic
# custom_mcp_failures alert below already catches any k8s_mcp tool failure,
# but doesn't distinguish "the underlying Connect Gateway connection itself
# failed" (RBAC/impersonation, SSL, network) from an ordinary Kubernetes-side
# error (pod not found, etc.). Filter built from a REAL observed failure log
# line (live-tested this session against the actual sre-lab cluster):
# `ERROR sre-mcp:security.py:405 guarded tool=list_pods failed:
# ...connectgateway.googleapis.com...` -- textPayload, same reasoning as the
# metric above (plain logging.basicConfig output, severity always DEFAULT).
# "failed:" scopes this to the real failure log line shape, not the (also
# connectgateway.googleapis.com-mentioning) successful-connection INFO line.
resource "google_logging_metric" "mcp_connect_gateway_failure" {
  name    = "sre_agent/mcp_connect_gateway_failure"
  project = var.project_a_id
  filter  = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"sre-k8s-mcp\" AND textPayload:\"guarded tool=\" AND textPayload:\"failed:\" AND textPayload:\"connectgateway.googleapis.com\""

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    display_name = "SRE Agent Custom MCP Connect Gateway Failure"
  }

  depends_on = [google_project_service.apis]
}

# Total investigation wall-clock latency, from the per-run structured log
# (jsonPayload.total_latency_s — rca_builder.py, measured against
# investigation["started_at"]). Backs the excessive-latency alert.
resource "google_logging_metric" "investigation_latency" {
  name            = "sre_agent/investigation_latency_seconds"
  project         = var.project_a_id
  filter          = "jsonPayload.event_type=\"sre_agent_run\" AND jsonPayload.total_latency_s > 0"
  value_extractor = "EXTRACT(jsonPayload.total_latency_s)"
  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "DISTRIBUTION"
    display_name = "SRE Agent Investigation Latency (seconds)"
    unit         = "s"
  }
  bucket_options {
    explicit_buckets {
      # max_duration_seconds default (state.py) is 540s — buckets run past that
      # so a timeout-triggered run still lands inside the histogram range.
      bounds = [5, 10, 20, 30, 60, 90, 120, 180, 300, 540, 600]
    }
  }
  depends_on = [google_project_service.apis]
}

# ── Notification channel + alert policies ──────────────────────────────────

# A newly-created log-based metric is not immediately queryable by Cloud
# Monitoring, so an alert policy referencing it can fail with "Cannot find
# metric(s)..." even with a depends_on. Wait for propagation before creating the
# alert policies. (On the rare occasion the metric still isn't visible, a
# re-apply converges — alert creation is idempotent.)
resource "time_sleep" "wait_for_metrics" {
  create_duration = "120s"

  depends_on = [
    google_logging_metric.errors,
    google_logging_metric.escalations,
    google_logging_metric.investigation_cost,
    google_logging_metric.token_usage,
    google_logging_metric.tool_failures,
    google_logging_metric.routing_failures,
    google_logging_metric.unresolved_cluster,
    google_logging_metric.evidence_storage_failures,
    google_logging_metric.investigation_latency,
    google_logging_metric.mcp_model_armor_fail_open,
    # 2026-09-08 fix: these two were added to iac/agent/monitoring.tf earlier
    # this session but never wired into this sleep's own depends_on -- their
    # alert policies correctly depend on time_sleep.wait_for_metrics, but the
    # sleep itself didn't wait on THEM, so it could finish before these two
    # metrics finished propagating in GCP. Confirmed live: first apply attempt
    # failed with "Cannot find metric(s) that match type ...
    # mcp_model_armor_guard_init_failed" / "...mcp_connect_gateway_failure".
    google_logging_metric.mcp_model_armor_guard_init_failed,
    google_logging_metric.mcp_connect_gateway_failure,
    # 2026-09-08: same gap, pre-existing (not introduced today) -- these three
    # each have an alert policy depending on this sleep but were never listed
    # here either. Harmless on a stable environment (already propagated long
    # ago), but a real landmine on a from-scratch apply. Closing the whole
    # class of bug rather than leaving it for the next fresh environment.
    google_logging_metric.invocations,
    google_logging_metric.confidence_band,
    google_logging_metric.loop_exit_reason,
  ]
}

resource "google_monitoring_notification_channel" "email_oncall" {
  project      = var.project_a_id
  display_name = "SRE Agent On-Call Email"
  type         = "email"
  labels = {
    email_address = var.notification_email
  }
  depends_on = [google_project_service.apis]
}

resource "google_monitoring_alert_policy" "high_error_rate" {
  project      = var.project_a_id
  display_name = "SRE Agent — High Error Rate"
  combiner     = "OR"
  conditions {
    display_name = "Agent errors > 5 in 5 minutes"
    condition_threshold {
      # resource.type corrected 2026-08-23: real sre_agent_run log entries carry
      # aiplatform.googleapis.com/ReasoningEngine, not "global" -- same source, same
      # fix already proven correct for token_usage_warning below. This alert never
      # fired before this fix; unverified until re-checked live against a real log.
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/errors\" AND resource.type=\"aiplatform.googleapis.com/ReasoningEngine\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email_oncall.name]
  documentation {
    content   = "SRE Agent error rate is elevated. Query logs: `jsonPayload.status=\"error\"`"
    mime_type = "text/markdown"
  }
  depends_on = [time_sleep.wait_for_metrics]
}

resource "google_monitoring_alert_policy" "high_escalation_rate" {
  project      = var.project_a_id
  display_name = "SRE Agent — High Escalation Rate"
  combiner     = "OR"
  conditions {
    display_name = "Escalations > 3 in 5 minutes"
    condition_threshold {
      # resource.type corrected 2026-08-23 -- see high_error_rate above for why.
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/escalations\" AND resource.type=\"aiplatform.googleapis.com/ReasoningEngine\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = 3
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email_oncall.name]
  documentation {
    content   = "SRE Agent is escalating incidents at a high rate. Query logs: `jsonPayload.confidence_band=\"escalate\"`"
    mime_type = "text/markdown"
  }
  depends_on = [time_sleep.wait_for_metrics]
}

resource "google_monitoring_alert_policy" "cost_spike" {
  project      = var.project_a_id
  display_name = "SRE Agent — Investigation Cost Spike"
  combiner     = "OR"
  conditions {
    display_name = "Single investigation cost > $0.10"
    condition_threshold {
      # resource.type corrected 2026-08-23 -- see high_error_rate above for why.
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/investigation_cost_usd\" AND resource.type=\"aiplatform.googleapis.com/ReasoningEngine\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0.10
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_PERCENTILE_99"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email_oncall.name]
  documentation {
    content   = "Single SRE Agent investigation exceeded $0.10. Check run_id in Cloud Logging for the token breakdown.\nQuery: `jsonPayload.estimated_cost_usd > 0.1`"
    mime_type = "text/markdown"
  }
  depends_on = [time_sleep.wait_for_metrics]
}

# Provider-neutral token-usage warning (issue #63) — replaces relying on the dollar-based
# cost_spike alert above as the only per-investigation anomaly signal. Threshold is
# derived entirely from config: var.max_tokens_per_run (agent/nodes/loop_controller.py's
# own hard cap, now Terraform-sourced) * var.token_warning_ratio (default 0.8) — computed
# once at apply time, never a static or empirically-guessed token number.
#
# This is a NEAR-BUDGET OPERATIONAL ALERT, not a real-time in-run warning -- the metric
# only counts agent/main.py's sre_agent_run event, which is written after the LangGraph
# run completes (confirmed: log-based metrics can also add their own propagation delay
# on top of that). It identifies COMPLETED investigations that landed close to the
# configured limit, so the threshold can be raised (or the agent's behavior tuned)
# before a FUTURE run actually hits loop_controller.py's hard cap -- it cannot warn
# during the specific investigation that triggers it.
#
# resource.type is aiplatform.googleapis.com/ReasoningEngine, not "global" like this
# file's other alerts -- confirmed with real log data (not assumed): a live
# sre_agent_run log entry's own `resource` field carries exactly this type, with
# reasoning_engine_id/location/resource_container labels (Cloud Logging's stdout
# capture for this Reasoning Engine tags it that way; a log-based metric's resulting
# time series inherits its source logs' resource type).
#
# enabled=false when max_tokens_per_run=0 -- a 0 * ratio threshold would be a
# meaningless always-firing (or nonsensical) alert; 0 means "hard cap disabled"
# (agent/nodes/loop_controller.py's own convention), so the warning must be disabled
# too, not silently left enabled with a broken threshold.
resource "google_monitoring_alert_policy" "token_usage_warning" {
  project      = var.project_a_id
  display_name = "SRE Agent — Investigation Token Usage Warning"
  combiner     = "OR"
  enabled      = var.max_tokens_per_run > 0
  conditions {
    display_name = "Single investigation tokens > ${var.token_warning_ratio * 100}% of max_tokens_per_run"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/investigation_tokens_total\" AND resource.type=\"aiplatform.googleapis.com/ReasoningEngine\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = var.max_tokens_per_run * var.token_warning_ratio
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_PERCENTILE_99"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email_oncall.name]
  documentation {
    content   = "A completed SRE Agent investigation used more than ${var.token_warning_ratio * 100}% of the configured max_tokens_per_run (${var.max_tokens_per_run} tokens) -- close to loop_controller.py's hard token_budget_exceeded cap. This is reported after the run finished, not during it; use it to catch a trend before a FUTURE run hits the hard cap. Check run_id in Cloud Logging.\nQuery: `jsonPayload.event_type=\"sre_agent_run\" AND jsonPayload.tokens_total > ${var.max_tokens_per_run * var.token_warning_ratio}`"
    mime_type = "text/markdown"
  }
  depends_on = [time_sleep.wait_for_metrics]
}

# ── PRODUCTION-LAUNCH-PLAN.md Priority 10 — the 8 missing alerts implemented ────────
#
# 11 alerts were named as missing in the plan. 8 are added below — the 8 most
# concrete/implementable given what the code can actually observe today.
# Originally 3 were deliberately SKIPPED (reasons recorded here rather than
# silently dropped); connect-gateway failures is no longer one of them as of
# 2026-09-08 (Section 11) -- see google_monitoring_alert_policy.
# mcp_connect_gateway_failure, added separately below. The remaining 2:
#
#   - PD webhook failures        — SKIPPED. No PagerDuty integration code exists yet
#     (Priority 2 is not started — confirmed by `grep -ri pagerduty agent/` finding
#     nothing but a docstring mention). There is no webhook to fail.
#   - connect-gateway failures   — NO LONGER SKIPPED as of 2026-09-08 (Section 11):
#     this comment previously said "no GKE Fleet / Connect Gateway code exists yet,"
#     which became false on 2026-09-07 (issue #86 / dynamic Connect Gateway
#     migration, see mcp/server.py's get_k8s_clients()). See
#     google_monitoring_alert_policy.mcp_connect_gateway_failure below for the
#     real alert this stale gap is now closed by.
#   - agent-gateway failures     — SKIPPED. agent_gateway.tf's IAP authz extension runs
#     in DRY_RUN (logs decisions, never blocks — see agent_gateway.tf's comment above
#     google_network_services_authz_extension.iap), and the gateway does not emit an
#     application-observable signal into the agent's own structured logs today. There
#     is currently no real failure mode to alert on without first switching the gateway
#     to enforce mode and instrumenting its own audit logs (separate log source, not
#     iac/agent/monitoring.tf's job) — tracked as follow-up, not faked here.
#
# The 8 implemented below: routing failures, unknown/ambiguous clusters, GKE-MCP
# failures, custom-MCP failures, excessive latency, repeated tool failures,
# loop/token termination, evidence-storage failures.

resource "google_monitoring_alert_policy" "routing_failures" {
  project      = var.project_a_id
  display_name = "SRE Agent — Routing Failures"
  combiner     = "OR"
  conditions {
    display_name = "mcp_router safe-stop > 0 in 5 minutes"
    condition_threshold {
      # resource.type corrected 2026-08-23: this metric sources from sre-agent-routing-failures,
      # written via cloud_logging.Client(_use_grpc=False) (issue #139 fix) -- confirmed live on
      # the identical client construction (sre-agent-investigations log) that these entries carry
      # resource.type=cloud_run_revision, NOT "global" and NOT aiplatform.googleapis.com/ReasoningEngine
      # (that latter type is only for entries from the separate stdout-based observability path).
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/routing_failures\" AND resource.type=\"cloud_run_revision\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email_oncall.name]
  documentation {
    content   = "mcp_router refused to guess an MCP destination for an already-resolved cluster (registry entry missing or disabled since context_resolver ran). See agent/nodes/mcp_router.py's safe-stop and PRODUCTION-LAUNCH-PLAN.md Priority 5/10.\nQuery: `logName=\"projects/${var.project_a_id}/logs/sre-agent-routing-failures\"`"
    mime_type = "text/markdown"
  }
  depends_on = [time_sleep.wait_for_metrics]
}

resource "google_monitoring_alert_policy" "unresolved_cluster" {
  project      = var.project_a_id
  display_name = "SRE Agent — Unknown/Ambiguous Cluster Safe-Stop"
  combiner     = "OR"
  conditions {
    display_name = "context_resolver unresolved-cluster safe-stop > 0 in 5 minutes"
    condition_threshold {
      # resource.type corrected 2026-08-23 -- see high_error_rate above for why.
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/unresolved_cluster\" AND resource.type=\"aiplatform.googleapis.com/ReasoningEngine\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email_oncall.name]
  documentation {
    content   = "context_resolver could not identify any cluster for an incident (Priority 5's deterministic routing priority chain fell through to the human safe-stop) and refused to guess. Likely a missing/incomplete alert payload or a cluster registry gap.\nQuery: `jsonPayload.cluster_routing_method=\"unresolved\"`"
    mime_type = "text/markdown"
  }
  depends_on = [time_sleep.wait_for_metrics]
}

resource "google_monitoring_alert_policy" "gke_mcp_failures" {
  project      = var.project_a_id
  display_name = "SRE Agent — GKE Remote MCP Failures"
  combiner     = "OR"
  conditions {
    display_name = "gke_remote_mcp tool failures > 3 in 5 minutes"
    condition_threshold {
      # resource.type corrected 2026-08-23 -- see routing_failures above for why (same
      # sre-agent-tool-failures logger, same cloud_logging.Client(_use_grpc=False) pattern).
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/tool_failures\" AND resource.type=\"cloud_run_revision\" AND metric.label.mcp_source=\"gke_remote_mcp\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = 3
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email_oncall.name]
  documentation {
    content   = "GKE Remote MCP (google-managed, primary source) is failing repeatedly. Check auth (ADC container.googleapis.com scope) and Preview/Pre-GA availability first.\nQuery: `logName=\"projects/${var.project_a_id}/logs/sre-agent-tool-failures\" AND jsonPayload.mcp_source=\"gke_remote_mcp\"`"
    mime_type = "text/markdown"
  }
  depends_on = [time_sleep.wait_for_metrics]
}

resource "google_monitoring_alert_policy" "custom_mcp_failures" {
  project      = var.project_a_id
  display_name = "SRE Agent — Custom K8s MCP Failures"
  combiner     = "OR"
  conditions {
    display_name = "k8s_mcp (custom Cloud Run MCP) tool failures > 3 in 5 minutes"
    condition_threshold {
      # resource.type corrected 2026-08-23 -- see routing_failures above for why (same
      # sre-agent-tool-failures logger, same cloud_logging.Client(_use_grpc=False) pattern).
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/tool_failures\" AND resource.type=\"cloud_run_revision\" AND metric.label.mcp_source=\"k8s_mcp\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = 3
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email_oncall.name]
  documentation {
    content   = "The custom read-only K8s MCP (Cloud Run fallback) is failing repeatedly. Check roles/run.invoker on the agent SA and the Cloud Run service health first.\nQuery: `logName=\"projects/${var.project_a_id}/logs/sre-agent-tool-failures\" AND jsonPayload.mcp_source=\"k8s_mcp\"`"
    mime_type = "text/markdown"
  }
  depends_on = [time_sleep.wait_for_metrics]
}

resource "google_monitoring_alert_policy" "excessive_latency" {
  project      = var.project_a_id
  display_name = "SRE Agent — Excessive Investigation Latency"
  combiner     = "OR"
  conditions {
    display_name = "p99 investigation latency > 180s"
    condition_threshold {
      # resource.type corrected 2026-08-23 -- see high_error_rate above for why.
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/investigation_latency_seconds\" AND resource.type=\"aiplatform.googleapis.com/ReasoningEngine\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 180
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_PERCENTILE_99"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email_oncall.name]
  documentation {
    content   = "Investigations are running long (p99 > 180s). Default hard timeout is max_duration_seconds=540s (agent/state.py) — this alert is meant to catch slow-creeping latency well before runs start hitting that hard timeout.\nQuery: `jsonPayload.total_latency_s > 180`"
    mime_type = "text/markdown"
  }
  depends_on = [time_sleep.wait_for_metrics]
}

resource "google_monitoring_alert_policy" "repeated_tool_failures" {
  project      = var.project_a_id
  display_name = "SRE Agent — Repeated Tool Failures (any source)"
  combiner     = "OR"
  conditions {
    display_name = "Tool failures > 5 in 10 minutes, any MCP source"
    condition_threshold {
      # Deliberately unfiltered by mcp_source — this is the aggregate/any-tool signal
      # the plan calls out as missing ("tool_failures metric exists but has no alert"),
      # distinct from the per-source gke_mcp_failures/custom_mcp_failures alerts above:
      # this one catches persistent flapping across BOTH sources that neither
      # per-source alert would individually cross threshold on.
      # resource.type corrected 2026-08-23 -- see routing_failures above for why (same
      # sre-agent-tool-failures logger, same cloud_logging.Client(_use_grpc=False) pattern).
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/tool_failures\" AND resource.type=\"cloud_run_revision\""
      duration        = "600s"
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      aggregations {
        alignment_period   = "600s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email_oncall.name]
  documentation {
    content   = "Repeated tool failures across MCP sources in a 10-minute window — a broader flapping signal than the per-source GKE/custom-MCP alerts.\nQuery: `logName=\"projects/${var.project_a_id}/logs/sre-agent-tool-failures\"`"
    mime_type = "text/markdown"
  }
  depends_on = [time_sleep.wait_for_metrics]
}

resource "google_monitoring_alert_policy" "loop_token_termination" {
  project      = var.project_a_id
  display_name = "SRE Agent — Abnormal Loop/Token Termination"
  combiner     = "OR"
  conditions {
    display_name = "Abnormal loop_exit_reason > 2 in 10 minutes"
    condition_threshold {
      # timeout | token_budget_exceeded | max_iterations | oscillation_detected |
      # stuck_detected — see agent/nodes/loop_controller.py. Excludes the two
      # "normal" exits (confidence_sufficient, tool_signaled_done) and
      # consecutive_tool_failures/zero_new_facts, which are already covered by the
      # tool-failure alerts above and would double-count with this one.
      # resource.type corrected 2026-08-23 -- see high_error_rate above for why.
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/loop_exit_reason\" AND resource.type=\"aiplatform.googleapis.com/ReasoningEngine\" AND metric.label.reason=monitoring.regex.full_match(\"timeout|token_budget_exceeded|max_iterations|oscillation_detected|stuck_detected\")"
      duration        = "600s"
      comparison      = "COMPARISON_GT"
      threshold_value = 2
      aggregations {
        alignment_period   = "600s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email_oncall.name]
  documentation {
    content   = "Investigations are exiting abnormally (timeout, token budget exceeded, max iterations, oscillation, or stuck) rather than reaching sufficient evidence or a clean router-signaled done. Query logs: `jsonPayload.loop_exit_reason=\"timeout\"` (or the other reasons above)."
    mime_type = "text/markdown"
  }
  depends_on = [time_sleep.wait_for_metrics]
}

resource "google_monitoring_alert_policy" "evidence_storage_failures" {
  project      = var.project_a_id
  display_name = "SRE Agent — Evidence Storage Failures"
  combiner     = "OR"
  conditions {
    display_name = "GCS evidence write failure > 0 in 5 minutes"
    condition_threshold {
      # resource.type corrected 2026-08-23 -- see routing_failures above for why (same
      # sre-agent-evidence-storage-failures logger, same cloud_logging.Client(_use_grpc=False) pattern).
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/evidence_storage_failures\" AND resource.type=\"cloud_run_revision\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email_oncall.name]
  documentation {
    content   = "An evidence item failed to write to GCS after retries — the audit chain is broken for that item. Check the EVIDENCE_BUCKET IAM binding and bucket existence first.\nQuery: `logName=\"projects/${var.project_a_id}/logs/sre-agent-evidence-storage-failures\"`"
    mime_type = "text/markdown"
  }
  depends_on = [time_sleep.wait_for_metrics]
}

# The response guard (mcp/response_guard.py) fails OPEN when Model Armor
# itself errors on a tool RESPONSE check -- this is the loud-alerting half of
# that decision (see the metric's own comment above for why fail-open is
# acceptable). Any occurrence means an investigation's tool responses went
# out unsanitized for that window; on-call should know within minutes, not
# find out from a later audit.
resource "google_monitoring_alert_policy" "mcp_model_armor_fail_open" {
  project      = var.project_a_id
  display_name = "SRE Agent — Custom MCP Model Armor Fail-Open"
  combiner     = "OR"
  conditions {
    display_name = "Response-guard Model Armor sanitize error > 0 in 5 minutes"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/mcp_model_armor_fail_open\" AND resource.type=\"cloud_run_revision\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email_oncall.name]
  documentation {
    content   = "The custom MCP's Model Armor response guard could not reach/complete a sanitize call and delivered the tool result unsanitized (fail-open, by design). Check Model Armor API health and mcp/response_guard.py's own error detail in the sre-k8s-mcp Cloud Run logs.\nQuery: `resource.type=\"cloud_run_revision\" resource.labels.service_name=\"sre-k8s-mcp\" textPayload:\"model_armor_fail_open\"`"
    mime_type = "text/markdown"
  }
  depends_on = [time_sleep.wait_for_metrics]
}

# Section 8 (2026-09-08): distinct from the alert above -- this fires on a
# STARTUP failure, not a per-call error. Any occurrence means the entire
# Cloud Run revision is running with sanitization permanently disabled until
# redeployed/restarted; this is a more severe, longer-lived condition than a
# single fail-open event and needs its own on-call signal, not just a shared
# metric with the per-call case.
resource "google_monitoring_alert_policy" "mcp_model_armor_guard_init_failed" {
  project      = var.project_a_id
  display_name = "SRE Agent — Custom MCP Model Armor Guard Init Failure"
  combiner     = "OR"
  conditions {
    display_name = "Response-guard Model Armor client failed to initialize > 0 in 5 minutes"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/mcp_model_armor_guard_init_failed\" AND resource.type=\"cloud_run_revision\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email_oncall.name]
  documentation {
    content   = "The custom MCP's Model Armor response guard failed to construct its client at process startup -- response sanitization is PERMANENTLY disabled for this Cloud Run revision until redeployed/restarted, not just for one call. Check MODEL_ARMOR_RESPONSE_TEMPLATE and IAM permissions, then redeploy.\nQuery: `resource.type=\"cloud_run_revision\" resource.labels.service_name=\"sre-k8s-mcp\" textPayload:\"model_armor_guard_init_failed\"`"
    mime_type = "text/markdown"
  }
  depends_on = [time_sleep.wait_for_metrics]
}

# Section 11 (2026-09-08): distinct from custom_mcp_failures below -- this
# specifically catches the underlying Connect Gateway connection itself
# failing (RBAC/impersonation rejection, TLS, network), separate from an
# ordinary Kubernetes-side error the same generic alert would also catch.
# Real, disclosed limitation of the underlying metric (not hidden): this
# fires on ANY Connect-Gateway-touching failure, including one caused by a
# caller's own identity lacking cluster RBAC (e.g. a human operator testing
# manually, as opposed to the deployed service's own identity) -- it cannot
# distinguish "the platform is broken" from "this specific caller isn't
# authorized," both look identical in the log text. Treat a single
# occurrence as "investigate," not "the whole path is down."
resource "google_monitoring_alert_policy" "mcp_connect_gateway_failure" {
  project      = var.project_a_id
  display_name = "SRE Agent — Custom MCP Connect Gateway Failure"
  combiner     = "OR"
  conditions {
    display_name = "Connect Gateway connection failure > 0 in 5 minutes"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/mcp_connect_gateway_failure\" AND resource.type=\"cloud_run_revision\""
      duration        = "0s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email_oncall.name]
  documentation {
    content   = "A custom-MCP tool call failed with an error that mentions connectgateway.googleapis.com -- likely an RBAC/impersonation rejection, TLS issue, or network problem reaching an on-prem cluster via Connect Gateway, distinct from an ordinary Kubernetes-side error. Check the exact error text before assuming platform-wide breakage -- this also fires for a caller identity that simply isn't authorized (e.g. manual testing), not only for real outages.\nQuery: `resource.type=\"cloud_run_revision\" resource.labels.service_name=\"sre-k8s-mcp\" textPayload:\"connectgateway.googleapis.com\" textPayload:\"failed:\"`"
    mime_type = "text/markdown"
  }
  depends_on = [time_sleep.wait_for_metrics]
}
