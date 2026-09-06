# Provider and Terraform version pins for the Investigation Dashboard stack.
# Matches ../gke-access/versions.tf's reasoning: company policy pins Terraform
# at 1.4.7, and this stack has no feature (check/import blocks, optional()
# defaults, precondition/postcondition) that needs anything newer.

terraform {
  required_version = ">= 1.4.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 7.0.0, < 8.0.0"
    }
  }
}
