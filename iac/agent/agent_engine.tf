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
      # Vertex AI. GOOGLE_CLOUD_LOCATION = the region (the official codelab uses the
      # regional endpoint ${REGION}-aiplatform.googleapis.com and does NOT set a
      # global model-endpoint-location). With IAP REQUEST_AUTHZ (no content
      # inspection / no TLS MITM) the gateway does not intercept these calls, so the
      # regional endpoint routes through and is IAP-authorized.
      GOOGLE_GENAI_USE_VERTEXAI = "True"
      GOOGLE_CLOUD_LOCATION     = var.region
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
#
# provider = google-beta: `spec.deployment_spec.agent_gateway_config` (below)
# only exists in google-beta (>= 7.40.0, see versions.tf) as of 2026-08-10 —
# confirmed absent from the plain `google` provider's schema at the same
# version. Scoped to THIS resource only — google_vertex_ai_reasoning_engine.
# memory_bank (below) has no gateway config and stays on the default `google`
# provider; nothing else in this stack changes provider.
resource "google_vertex_ai_reasoning_engine" "sre_agent" {
  provider = google-beta

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

      # Phase 2 of the attach_gateway_to_engine.sh -> Terraform migration
      # (2026-08-10, see docs/ADR-002 and the rollback tag
      # rollback-pre-agent-gateway-config-tf-2026-08-10). Binds this engine's
      # OUTBOUND MCP traffic to the existing AGENT_TO_ANYWHERE gateway
      # (agent_gateway.tf) — the exact same binding the script has performed
      # out-of-band since the gateway was introduced. Guarded by
      # var.enable_agent_gateway so a gateway-disabled deployment never
      # references google_network_services_agent_gateway.sre_egress[0] when
      # its count is 0.
      #
      # attach_gateway_to_engine.sh is NOT removed by this change (see the
      # script's own header) — it stays available as the rollback path until
      # both Phase 3/4 live tests (gateway binding, then a source-only update
      # with the binding unchanged) pass against a real deployment.
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
    google_vertex_ai_reasoning_engine.memory_bank,
  ]
}
