# AI SRE Agent — Investigation Dashboard backend.
#
# Architecture (verified against real Cloud Logging -> BigQuery sink behavior,
# not assumed — see PHASE1_EVIDENCE_LOG.md-style research notes in the PR):
#
#   agent/nodes/rca_builder.py's "sre-agent-investigations" logger  ─┐
#   Model Armor's "sanitize_operations" log                          ├─sink─▶ BigQuery (this dataset)
#   Agent Gateway's "gateway_requests" log                          ─┘
#
# Each source gets its OWN Cloud Logging sink and its OWN raw table — Cloud
# Logging derives the raw table name from the log's own ID (sanitized), NOT
# from the sink's Terraform name, and there is no Terraform argument to
# override that. `use_partitioned_tables = true` on every sink is what makes
# each raw table a single, stable-named table (day-partitioned internally by
# BigQuery) instead of a `_YYYYMMDD`-sharded table per day — required for the
# normalized views (added in a follow-up apply, once the real sanitized table
# names are confirmed against this project's actual dataset contents) to
# reference one fixed table name each, not a wildcard.
#
# Read-only, additive-only observability. This stack creates NO compute, NO
# always-on resources — a BigQuery dataset and Cloud Logging sinks are pure
# metadata/routing constructs with no idle cost.

resource "google_bigquery_dataset" "dashboard" {
  dataset_id  = var.bigquery_dataset_id
  project     = var.project_a_id
  location    = var.region
  description = "Investigation Dashboard — normalized SRE Agent investigation telemetry (Phase: dashboard build, 2026-09-05)."

  # Applies to every table in the dataset, including the ones Cloud Logging's
  # sinks create automatically (not just tables/views this Terraform manages
  # directly) — a Terraform variable, not hardcoded, so a work/production
  # environment can set a longer retention without editing this stack's code.
  default_partition_expiration_ms = var.partition_expiration_days * 24 * 60 * 60 * 1000

  labels = {
    purpose = "sre-agent-investigation-dashboard"
  }
}

# ── Sink 1: investigation-level structured log (the primary per-run source) ──
#
# agent/nodes/rca_builder.py's "sre-agent-investigations" logger, PLUS
# agent/main.py's crash-terminal-event (added 2026-09-05, same logger name,
# same BigQuery table) for runs that crash before rca_builder ever runs.
resource "google_logging_project_sink" "investigations" {
  name        = "sre-agent-investigations-to-bq"
  project     = var.project_a_id
  destination = "bigquery.googleapis.com/projects/${var.project_a_id}/datasets/${google_bigquery_dataset.dashboard.dataset_id}"

  # log_id() matches only this specific structured log, not every log in the
  # project — same function-call filter syntax Google's own sink docs use.
  filter = "log_id(\"sre-agent-investigations\")"

  unique_writer_identity = true
  bigquery_options {
    use_partitioned_tables = true
  }
}

resource "google_bigquery_dataset_iam_member" "investigations_sink_writer" {
  dataset_id = google_bigquery_dataset.dashboard.dataset_id
  project    = var.project_a_id
  role       = "roles/bigquery.dataEditor"
  member     = google_logging_project_sink.investigations.writer_identity
}

# ── Sink 2: Model Armor activity (aggregate/time-series only — no run_id) ──
#
# Covers all 3 Model Armor mechanisms (floor setting, app-level, CONTENT_AUTHZ
# gateway extension) since they share one log, distinguished downstream by
# resource.labels.template_id — never conflated into one "Model Armor status"
# per the accepted Phase 1 CONTENT_AUTHZ limitation (see PHASE1_EVIDENCE_LOG.md).
resource "google_logging_project_sink" "model_armor_activity" {
  name        = "model-armor-activity-to-bq"
  project     = var.project_a_id
  destination = "bigquery.googleapis.com/projects/${var.project_a_id}/datasets/${google_bigquery_dataset.dashboard.dataset_id}"
  filter      = "log_id(\"modelarmor.googleapis.com/sanitize_operations\")"

  unique_writer_identity = true
  bigquery_options {
    use_partitioned_tables = true
  }
}

resource "google_bigquery_dataset_iam_member" "model_armor_sink_writer" {
  dataset_id = google_bigquery_dataset.dashboard.dataset_id
  project    = var.project_a_id
  role       = "roles/bigquery.dataEditor"
  member     = google_logging_project_sink.model_armor_activity.writer_identity
}

# ── Sink 3: Agent Gateway activity (aggregate/time-series only — no run_id) ──
#
# ALLOW/DENY trends and routing failures for the Reliability & Security page.
# No per-investigation join is possible (confirmed, see the Phase A telemetry
# gap analysis) — correlation to a specific run is timestamp+hostname only,
# and this dashboard must not fake a per-run link this log can't support.
resource "google_logging_project_sink" "gateway_activity" {
  name        = "gateway-activity-to-bq"
  project     = var.project_a_id
  destination = "bigquery.googleapis.com/projects/${var.project_a_id}/datasets/${google_bigquery_dataset.dashboard.dataset_id}"
  filter      = "log_id(\"networkservices.googleapis.com/gateway_requests\")"

  unique_writer_identity = true
  bigquery_options {
    use_partitioned_tables = true
  }
}

resource "google_bigquery_dataset_iam_member" "gateway_sink_writer" {
  dataset_id = google_bigquery_dataset.dashboard.dataset_id
  project    = var.project_a_id
  role       = "roles/bigquery.dataEditor"
  member     = google_logging_project_sink.gateway_activity.writer_identity
}

# ── Least-privilege read access for the dashboard's own viewer/builder ──
#
# Dataset-level dataViewer (not project-wide, not public) plus project-level
# jobUser (BigQuery's query-execution permission is project-scoped, not
# dataset-scoped — this does NOT grant access to any OTHER dataset's data,
# only the ability to run a query job at all, still gated by the dataViewer
# grant above for what data that job can actually read).
resource "google_bigquery_dataset_iam_member" "dashboard_viewer" {
  dataset_id = google_bigquery_dataset.dashboard.dataset_id
  project    = var.project_a_id
  role       = "roles/bigquery.dataViewer"
  member     = "user:${var.dashboard_viewer_email}"
}

resource "google_project_iam_member" "dashboard_viewer_job_user" {
  project = var.project_a_id
  role    = "roles/bigquery.jobUser"
  member  = "user:${var.dashboard_viewer_email}"
}
