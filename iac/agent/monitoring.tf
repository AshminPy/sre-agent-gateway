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

resource "google_logging_metric" "invocations" {
  name    = "sre_agent/invocations"
  project = var.project_a_id
  filter  = "jsonPayload.run_id:*"
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
  filter  = "jsonPayload.status=\"error\""
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
  filter  = "jsonPayload.confidence_band=\"escalate\""
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
  filter          = "jsonPayload.estimated_cost_usd > 0"
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

resource "google_logging_metric" "confidence_band" {
  name             = "sre_agent/confidence_band"
  project          = var.project_a_id
  filter           = "jsonPayload.confidence_band != \"\""
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
  filter           = "jsonPayload.loop_exit_reason != \"\""
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
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/errors\" AND resource.type=\"global\""
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
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/escalations\" AND resource.type=\"global\""
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
      filter          = "metric.type=\"logging.googleapis.com/user/sre_agent/investigation_cost_usd\" AND resource.type=\"global\""
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
