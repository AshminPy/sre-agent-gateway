# Provider and Terraform version pins.
#
# The Agent Engine (google_vertex_ai_reasoning_engine) and Agent Gateway
# (google_network_services_agent_gateway, authz extensions/policies) resources
# require the 7.x major of the Google providers. google-beta is needed for the
# Model Armor template and the IAP agent-registry IAM resources.

terraform {
  required_version = ">= 1.6.0"

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
