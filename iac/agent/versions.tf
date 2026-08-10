# Provider and Terraform version pins.
#
# The Agent Engine (google_vertex_ai_reasoning_engine) and Agent Gateway
# (google_network_services_agent_gateway, authz extensions/policies) resources
# require the 7.x major of the Google providers. google-beta is needed for the
# Model Armor template, the IAP agent-registry IAM resources, and (as of
# 2026-08-10) the Reasoning Engine's `agent_gateway_config` field.
#
# google-beta >= 7.40.0: `agent_gateway_config` (spec.deployment_spec block)
# was added to google_vertex_ai_reasoning_engine in google-beta v7.40.0
# (2026-07-14, hashicorp/terraform-provider-google-beta#12671) — confirmed
# directly against that tag's real CHANGELOG.md and the resource's Go schema
# (grep for "agent_gateway_config" in resource_vertex_ai_reasoning_engine.go:
# present in google-beta, ABSENT from the plain google provider at the same
# version — this field is google-beta-only today). 7.39.0 (previously locked)
# predates this field and cannot parse it — bumping the FLOOR, not just the
# lock file, so `terraform init` can never silently resolve back below 7.40.0.
#
# `google` (non-beta) floor bumped to match, even though no google-provider
# resource in this stack uses the new field — the two providers are used
# together in one configuration and are kept version-aligned on purpose, so
# they can't silently drift into an untested combination.
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
      version = ">= 7.40.0, < 8.0.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 7.40.0, < 8.0.0"
    }
    time = {
      source  = "hashicorp/time"
      version = ">= 0.11.0"
    }
  }
}
