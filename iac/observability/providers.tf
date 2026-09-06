# This stack reads the agent's own logs (Cloud Logging) and writes a normalized
# copy into BigQuery, all inside Project A (where the agent, its Cloud Run MCP,
# Agent Gateway, and Model Armor templates already live). No cross-project
# access needed — every log source this stack sinks from already lives here.

provider "google" {
  project = var.project_a_id
  region  = var.region
}
