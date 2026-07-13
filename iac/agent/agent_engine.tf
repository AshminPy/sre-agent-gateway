# The agent itself: a Vertex AI Agent Engine (reasoning engine) running the
# LangGraph SRE investigator, plus a second reasoning engine used purely as a
# persistent Memory Bank.
#
# Deployment method: source files packaged into agent.tar.gz and embedded inline
# via filebase64() — no staging bucket. Build the archive before plan/apply with
# `make package-agent` (CI does this automatically before terraform plan).

# Vertex AI platform service agent needs this project-level role before an Agent
# Engine can be created.
resource "google_project_iam_member" "vertex_ai_service_agent" {
  project = var.project_a_id
  role    = "roles/aiplatform.serviceAgent"
  member  = "serviceAccount:${google_project_service_identity.aiplatform.email}"

  depends_on = [google_project_service_identity.aiplatform]
}

# Persistent Memory Bank — a source-less reasoning engine used as long-term
# storage. The SRE agent reads prior RCAs at startup and writes one per run.
resource "google_vertex_ai_reasoning_engine" "memory_bank" {
  project      = var.project_a_id
  region       = var.region
  display_name = "sre-agent-memory-bank"
  description  = "Long-term memory store for the SRE agent — persists RCA findings across investigations."

  timeouts {
    create = "30m"
    update = "30m"
    delete = "10m"
  }

  depends_on = [google_project_iam_member.vertex_ai_service_agent]
}

# Single source of truth for the agent's runtime environment.
locals {
  agent_env = merge(
    {
      PROJECT_ID                                 = var.project_a_id
      REGION                                     = var.region
      GEMINI_MODEL                               = var.gemini_model
      MODEL_ARMOR_TEMPLATE                       = google_model_armor_template.sre_agent.name
      EVAL_BUCKET                                = "gs://${google_storage_bucket.eval.name}"
      EVIDENCE_BUCKET                            = google_storage_bucket.evidence.name
      CLUSTER_CONFIG_BUCKET                      = google_storage_bucket.cluster_config.name
      MEMORY_BANK_RESOURCE                       = google_vertex_ai_reasoning_engine.memory_bank.id
      GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY = "true"
    },
    # The engine always runs as an Agent Identity (required for the gateway),
    # whose tokens are DPoP-bound by default. This opt-out lets the agent's own
    # SDK calls to Google services (Gemini, Model Armor, Memory Bank) authenticate
    # — needed in BOTH gateway modes since the identity type is the same. Matches
    # the codelab's --allow-token-sharing.
    {
      GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES = "false"
    },
    # Point the fallback MCP at the custom Cloud Run service when enabled.
    var.enable_custom_mcp ? {
      K8S_MCP_URL = google_cloud_run_v2_service.mcp[0].uri
    } : {},
  )
}

# The SRE agent.
resource "google_vertex_ai_reasoning_engine" "sre_agent" {
  project      = var.project_a_id
  region       = var.region
  display_name = "sre-agent-gcp"
  description  = "SRE AI Investigation Co-pilot — LangGraph on Vertex AI Agent Engine."

  spec {
    # AGENT_IDENTITY gives the engine a per-agent SPIFFE identity (required to
    # attach it to the Agent Gateway). It is mutually exclusive with
    # service_account, which must NOT be set. All runtime roles are granted to
    # the resulting principal (see iam.tf, local.agent_identity_member).
    identity_type = "AGENT_IDENTITY"

    source_code_spec {
      inline_source {
        # Built by `make package-agent` (or CI) before plan/apply.
        source_archive = filebase64("${path.module}/../../agent.tar.gz")
      }
      python_spec {
        entrypoint_module = "agent.main"
        entrypoint_object = "SREAgent"
        version           = "3.11"
        requirements_file = "agent/requirements.txt"
      }
    }

    deployment_spec {
      dynamic "env" {
        for_each = local.agent_env
        content {
          name  = env.key
          value = env.value
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
    google_model_armor_template.sre_agent,
    google_storage_bucket.evidence,
    google_storage_bucket.eval,
    google_storage_bucket.cluster_config,
    google_vertex_ai_reasoning_engine.memory_bank,
  ]
}
