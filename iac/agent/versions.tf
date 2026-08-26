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
  # 2026-08-26: lowered 1.9.0 -> 1.4.7 to match company Spacelift's pinned
  # version, so this repo and sre-agent-app-infra stay deployable on the same
  # Terraform and cannot drift apart.
  #
  # The 1.9.0 floor existed for ONE reason: var.additional_clusters' collision
  # guard was a cross-variable `validation` block referencing
  # var.gke_cluster_name, which needs 1.9+. That guard now lives as a
  # `lifecycle.precondition` on google_storage_bucket_object.clusters_json
  # (buckets.tf) — available since Terraform 1.2, same fail-on-plan/apply
  # behaviour, same condition. Nothing else in this stack uses a 1.5+ feature:
  # no check blocks, no import blocks, no removed blocks, no .tftest.hcl, no
  # provider-defined functions. CI is pinned to 1.4.7 to match
  # (.github/workflows/terraform-*.yml, claude-merge-gate.yml).
  required_version = ">= 1.4.7"

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
