# A/B candidate agent slot (openspec/changes/candidate-agent-ab).
#
# An optional SECOND Agent Engine that runs a candidate build of the agent side
# by side with the primary `sre_agent`, in the same environment: same gateway,
# Model Armor templates, buckets, cluster registry and GKE access. Used to
# measure a candidate on the same eval cases before it replaces the primary.
#
# Off by default. Every resource here is count/for_each-gated on
# var.enable_candidate_agent, and none of them modifies a primary resource:
# the candidate's grants are declared separately (DECISION in the change's
# design.md) so `terraform plan` for the primary agent stays empty.
#
# Candidate source is packaged by `make package-candidate` into the git-ignored
# agent-candidate.tar.gz -- candidate code is never committed to this repo.
#
# Cross-project GKE IAM needs nothing here: iac/gke-access grants the
# project-wide agent principalSet, which already covers this engine. The
# namespace RBAC in k8s/rbac.yaml binds individual principals, so the candidate
# principal (output `candidate_agent_identity_member`) is added there after the
# first apply.
#
# Rollback: enable_candidate_agent = false + apply (removes only these).
#
# CI interaction: .github/workflows/terraform-apply.yml does not pass
# enable_candidate_agent, so the NEXT CI apply from main removes a locally
# applied candidate (engine, memory bank and its memories, grants). The
# primary is unaffected. An A/B run must therefore finish before the next
# push to main -- or re-apply the candidate afterwards.

locals {
  candidate_count = var.enable_candidate_agent ? 1 : 0

  candidate_engine_numeric_id = var.enable_candidate_agent ? element(reverse(split("/", google_vertex_ai_reasoning_engine.sre_agent_candidate[0].id)), 0) : ""

  candidate_identity_member = var.enable_candidate_agent ? "principal://agents.global.org-${data.google_project.a.org_id}.system.id.goog/resources/aiplatform/projects/${data.google_project.a.number}/locations/${var.region}/reasoningEngines/${local.candidate_engine_numeric_id}" : ""

  # Same environment as the primary; only the memory bank differs, so the
  # candidate never reads memories the primary wrote (and vice versa) -- a
  # shared bank would make the A/B result depend on the other agent's history.
  candidate_agent_env = var.enable_candidate_agent ? merge(local.agent_env, {
    MEMORY_BANK_RESOURCE = google_vertex_ai_reasoning_engine.memory_bank_candidate[0].id
  }) : {}
}

resource "google_vertex_ai_reasoning_engine" "memory_bank_candidate" {
  count = local.candidate_count

  project      = var.project_a_id
  region       = var.region
  display_name = "sre-agent-candidate-memory-bank"
  description  = "Memory store for the A/B candidate SRE agent -- isolated from the primary agent's memory bank."

  timeouts {
    create = "30m"
    update = "30m"
    delete = "10m"
  }

  depends_on = [google_project_iam_member.vertex_ai_service_agent]
}

resource "google_vertex_ai_reasoning_engine" "sre_agent_candidate" {
  count    = local.candidate_count
  provider = google-beta

  project      = var.project_a_id
  region       = var.region
  display_name = "sre-agent-candidate"
  description  = "A/B candidate build of the SRE agent, evaluated side by side with sre-agent-gcp."

  spec {
    identity_type = "AGENT_IDENTITY"

    source_code_spec {
      inline_source {
        # Guarded: `terraform validate` evaluates filebase64 even at count = 0, and
        # CI never builds this archive. Not try(): with the flag ON a missing
        # archive must fail loudly, never deploy an empty one.
        source_archive = var.enable_candidate_agent ? filebase64("${path.module}/../../agent-candidate.tar.gz") : ""
      }
      python_spec {
        entrypoint_module = "agent.main"
        entrypoint_object = "SREAgent"
        version           = "3.11"
        requirements_file = "agent/requirements.txt"
      }
    }

    deployment_spec {
      # Same scaling as the primary (see agent_engine.tf): no idle cost.
      min_instances   = 0
      max_instances   = 10
      resource_limits = { cpu = "4", memory = "8Gi" }

      dynamic "env" {
        for_each = local.candidate_agent_env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "agent_gateway_config" {
        for_each = var.enable_agent_gateway ? [1] : []
        content {
          agent_to_anywhere_config {
            agent_gateway = google_network_services_agent_gateway.sre_egress[0].id
          }
        }
      }
    }
  }

  timeouts {
    create = "30m"
    update = "30m"
    delete = "10m"
  }

  depends_on = [
    google_project_iam_member.vertex_ai_service_agent,
    google_model_armor_template.sre_agent_request,
    google_model_armor_template.sre_agent_response,
    google_storage_bucket.evidence,
    google_storage_bucket.eval,
    google_storage_bucket.cluster_config,
    google_vertex_ai_reasoning_engine.memory_bank_candidate,
  ]
}

# ── Candidate identity grants: exactly the primary's runtime list (iam.tf) ──

resource "google_project_iam_member" "candidate_runtime_identity" {
  for_each = var.enable_candidate_agent ? toset(local.runtime_project_roles) : toset([])

  project = var.project_a_id
  role    = each.value
  member  = local.candidate_identity_member
}

resource "google_project_iam_member" "candidate_runtime_model_armor_user" {
  count = var.enable_candidate_agent && !var.enable_agent_gateway ? 1 : 0

  project = var.project_a_id
  role    = "roles/modelarmor.user"
  member  = local.candidate_identity_member
}

resource "google_storage_bucket_iam_member" "candidate_runtime_bucket" {
  for_each = var.enable_candidate_agent ? {
    evidence_writer       = { bucket = google_storage_bucket.evidence.name, role = "roles/storage.objectCreator" }
    evidence_reader       = { bucket = google_storage_bucket.evidence.name, role = "roles/storage.objectViewer" }
    eval_writer           = { bucket = google_storage_bucket.eval.name, role = "roles/storage.objectCreator" }
    eval_reader           = { bucket = google_storage_bucket.eval.name, role = "roles/storage.objectViewer" }
    cluster_config_reader = { bucket = google_storage_bucket.cluster_config.name, role = "roles/storage.objectViewer" }
  } : {}

  bucket = each.value.bucket
  role   = each.value.role
  member = local.candidate_identity_member
}

resource "google_cloud_run_v2_service_iam_member" "candidate_runtime_invoke_mcp" {
  count = var.enable_candidate_agent && var.enable_custom_mcp ? 1 : 0

  project  = var.project_a_id
  location = var.region
  name     = google_cloud_run_v2_service.mcp[0].name
  role     = "roles/run.invoker"
  member   = local.candidate_identity_member
}

resource "google_iap_agent_registry_iam_member" "candidate_agent_egressor" {
  count    = var.enable_candidate_agent && var.enable_agent_gateway ? 1 : 0
  provider = google-beta

  project  = var.project_a_id
  location = var.region
  role     = "roles/iap.egressor"
  member   = local.candidate_identity_member
}

output "candidate_reasoning_engine_id" {
  description = "Full resource name of the A/B candidate engine (empty when disabled)."
  value       = var.enable_candidate_agent ? google_vertex_ai_reasoning_engine.sre_agent_candidate[0].id : ""
}

output "candidate_agent_identity_member" {
  description = "Candidate engine's Agent Identity principal -- add to k8s/rbac.yaml subjects."
  value       = local.candidate_identity_member
}
