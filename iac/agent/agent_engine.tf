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
    # ── This agent's own functional config ──────────────────────────────────
    {
      PROJECT_ID            = var.project_a_id
      REGION                = var.region
      GEMINI_MODEL          = var.gemini_model
      EVAL_BUCKET           = "gs://${google_storage_bucket.eval.name}"
      EVIDENCE_BUCKET       = google_storage_bucket.evidence.name
      CLUSTER_CONFIG_BUCKET = google_storage_bucket.cluster_config.name
      MEMORY_BANK_RESOURCE  = google_vertex_ai_reasoning_engine.memory_bank.id
    },
    # ── EXACT mirror of the proven-working codelab agent's env_vars ──────────
    # Source: agent-gateway codelab src/mortgage-agent/deploy_agent.py env_vars.
    {
      # Vertex AI + model endpoint. GOOGLE_CLOUD_LOCATION pins the model endpoint
      # location; "global" (the codelab default) avoids the regional mTLS Vertex
      # endpoint, which the Agent Gateway's TLS inspection cannot handle.
      GOOGLE_GENAI_USE_VERTEXAI = "True"
      GOOGLE_CLOUD_LOCATION     = var.model_endpoint_location
      # Agent Identity DPoP token-sharing opt-out (codelab's --allow-token-sharing).
      GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES = "false"
      # Telemetry ON, with the OTEL config the codelab pairs with it (telemetry ON
      # *without* this OTEL config is what produced the OTLP "Context has already
      # been used to create a Connection" error).
      GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY         = "true"
      OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT = "true"
      OTEL_TRACES_SAMPLER                                = "parentbased_traceidratio"
      OTEL_TRACES_SAMPLER_ARG                            = "1.0"
      # Make gateway-denied (403) MCP tool calls fail fast instead of hanging the
      # turn as a broken-stream TaskGroup/TimeoutError.
      ADK_ENABLE_MCP_GRACEFUL_ERROR_HANDLING = "true"
    },
    # ── App-level Model Armor: gateway-OFF only ─────────────────────────────
    # Under the gateway the Model Armor CONTENT_AUTHZ extension inspects egress,
    # so app-level sanitize would be a redundant second (gateway-routed) call.
    # The codelab agent does not set it under the gateway; agent code skips it.
    var.enable_agent_gateway ? {} : {
      MODEL_ARMOR_TEMPLATE = google_model_armor_template.sre_agent_request.name
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
      # Match the codelab agent's deploy_config: min 2 warm instances + 4 vCPU /
      # 8Gi so the runtime is provisioned the same way (deploy_agent.py).
      min_instances   = 2
      resource_limits = { cpu = "4", memory = "8Gi" }

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
    google_model_armor_template.sre_agent_request,
    google_model_armor_template.sre_agent_response,
    google_storage_bucket.evidence,
    google_storage_bucket.eval,
    google_storage_bucket.cluster_config,
    google_vertex_ai_reasoning_engine.memory_bank,
  ]
}
