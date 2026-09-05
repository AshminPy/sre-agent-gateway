variable "project_a_id" {
  description = "Project A (where the agent, its logs, Cloud Run MCP, Agent Gateway, and Model Armor templates all live). This stack reads those logs and writes into BigQuery in the same project."
  type        = string
}

variable "region" {
  description = "Region for the BigQuery dataset. Matches the agent stack's own region for locality/latency, not a hard requirement."
  type        = string
  default     = "us-central1"
}

variable "bigquery_dataset_id" {
  description = "BigQuery dataset ID that holds the raw log-sink tables and the normalized dashboard views."
  type        = string
  default     = "sre_agent_investigations"
}

variable "partition_expiration_days" {
  description = "BigQuery partition expiration, in days, for every table in this dataset. 90 for this personal POC (deliberately a variable, not hardcoded, so a work/production environment can set a different retention without editing this stack's code)."
  type        = number
  default     = 90
}

variable "dashboard_viewer_email" {
  description = "Email (Google Account) granted read-only (roles/bigquery.dataViewer) access to the dataset — the identity that will build/view the Looker Studio report. Required, no default, so no personal email ships as a checked-in default."
  type        = string
}
