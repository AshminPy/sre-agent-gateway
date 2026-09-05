output "bigquery_dataset_id" {
  value = google_bigquery_dataset.dashboard.dataset_id
}

output "bigquery_dataset_self_link" {
  value = google_bigquery_dataset.dashboard.self_link
}

output "investigations_sink_writer_identity" {
  value = google_logging_project_sink.investigations.writer_identity
}

output "model_armor_sink_writer_identity" {
  value = google_logging_project_sink.model_armor_activity.writer_identity
}

output "gateway_sink_writer_identity" {
  value = google_logging_project_sink.gateway_activity.writer_identity
}
