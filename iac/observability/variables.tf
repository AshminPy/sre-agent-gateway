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

variable "dashboard_timezone" {
  description = "IANA time zone used to derive the `event_date_local` DATE column in every dashboard view (e.g. \"America/New_York\"). Looker Studio evaluates relative date ranges such as \"Last 7 days (include today)\" against a DATE, and the raw `timestamp` is UTC — so without a local date, runs logged after 00:00 UTC vanish from \"today\" until the local date rolls over. Set this to the timezone of the people reading the report, not of the cluster."
  type        = string
  default     = "America/New_York"

  validation {
    condition     = can(regex("^[A-Za-z_]+(/[A-Za-z_+-]+)*$", var.dashboard_timezone))
    error_message = "dashboard_timezone must be an IANA zone name such as America/New_York or UTC."
  }
}

variable "dashboard_viewer_email" {
  description = "Email (Google Account) granted read-only (roles/bigquery.dataViewer) access to the dataset — the identity that will build/view the Looker Studio report. Required, no default, so no personal email ships as a checked-in default."
  type        = string
}
