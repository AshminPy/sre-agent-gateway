# Provider and Terraform version pins.
#
# The Agent Engine (google_vertex_ai_reasoning_engine) and Agent Gateway
# (google_network_services_agent_gateway, authz extensions/policies) resources
# require the 7.x major of the Google providers. google-beta is needed for the
# Model Armor template and the IAP agent-registry IAM resources.

terraform {
  # >= 1.9.0 (bumped from 1.6.0): var.additional_clusters' collision guard
  # (variables.tf) is a cross-variable `validation` block referencing
  # var.gke_cluster_name — that capability requires Terraform 1.9+. CI already
  # pins exactly 1.9.0 (.github/workflows/terraform-*.yml), so this doesn't
  # tighten anything CI wasn't already using.
  required_version = ">= 1.9.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 7.0.0, < 8.0.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 7.0.0, < 8.0.0"
    }
    time = {
      source  = "hashicorp/time"
      version = ">= 0.11.0"
    }
  }
}
