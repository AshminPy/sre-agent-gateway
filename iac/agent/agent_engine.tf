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
      PROJECT_ID   = var.project_a_id
      REGION       = var.region
      GEMINI_MODEL = var.gemini_model
      # Same var as GEMINI_MODEL above, deliberately — LLM_PROFILE (agent/llm/registry.py)
      # is the provider-neutral selector issue #63 introduced; GEMINI_MODEL is kept for
      # the Gemini adapter's own model-name resolution and existing tooling. Both must
      # always name the same model, so both are set from this one variable rather than
      # two separate ones that could drift apart.
      LLM_PROFILE         = var.gemini_model
      GEMINI_PRICE_INPUT  = tostring(var.gemini_price_input_per_1m)
      GEMINI_PRICE_OUTPUT = tostring(var.gemini_price_output_per_1m)
      # agent/nodes/loop_controller.py has read this env var since before this change
      # (hard token cap, Python-side default 100000) -- Terraform never set it, so
      # production always silently ran on that default. var.max_tokens_per_run's own
      # default matches it exactly, so this addition alone changes no live behavior;
      # it only makes the value configurable + gives iac/agent/monitoring.tf's new
      # token-usage warning alert the same number to derive its threshold from.
      MAX_TOKENS_PER_RUN    = tostring(var.max_tokens_per_run)
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
      # issue #94: GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY intentionally NOT declared
      # here. Confirmed against the actual google provider v7.43.0 source
      # (resource_vertex_ai_reasoning_engine.go): expandVertexAIReasoningEngineSpecDeploymentSpecEnv
      # passes this var through like any other on create/update, but
      # flattenVertexAIReasoningEngineSpecDeploymentSpecEnv unconditionally `continue`s past
      # it when reading state back -- so as long as we declare it, Terraform's own state can
      # never record it as satisfied, and every plan re-proposes adding it forever (never a
      # real "0 changes", confirmed on every plan this session including #103's PR #156).
      # Removing the declaration makes config and (always-telemetry-stripped) refreshed state
      # agree -- 0 diff on this attribute, so a real apply issues no update for it at all,
      # leaving the live resource's already-set value (from all prior applies) untouched.
      # The OTEL config below is unrelated -- kept exactly as-is.
      # issue #76: this flag (standard OTel GenAI semantic-convention instrumentation)
      # included the actual prompt/response TEXT in trace spans -- built from real k8s
      # evidence (pod logs, events), at 100% sampling, live and undocumented. Disabled
      # deliberately after review: Cloud Trace stays as token-count/latency/span-structure
      # tracing only (OTEL_TRACES_SAMPLER/_ARG below are UNCHANGED -- that's sampling
      # rate, a separate concern from content capture, and normal performance tracing is
      # still wanted at full rate). See docs/trace-content-capture.md.
      OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT = "false"
      OTEL_TRACES_SAMPLER                                = "parentbased_traceidratio"
      OTEL_TRACES_SAMPLER_ARG                            = "1.0"
      # Make gateway-denied (403) MCP tool calls fail fast instead of hanging the
      # turn as a broken-stream TaskGroup/TimeoutError.
      ADK_ENABLE_MCP_GRACEFUL_ERROR_HANDLING = "true"
      # TEMPORARY diagnostic marker (2026-09-05, #203 CONTENT_AUTHZ
      # investigation) -- forces a fresh reasoning-engine redeploy so the
      # next test doesn't reuse a >1hr-old warm container whose connection to
      # the custom MCP appears to bypass Agent Gateway's per-request
      # interception once established (custom-MCP calls stopped appearing in
      # gateway logs ~40min into that container's uptime, while other
      # traffic kept logging normally). Remove once this investigation
      # concludes either way.
      PHASE1_CONTENT_AUTHZ_FORCE_FRESH_CONTAINER = "2026-09-05-01"
    },
    # ── App-level Model Armor: gateway-OFF only (issue #203, PARTIALLY reverted 2026-09-05) ──
    # Attempted to make this unconditional (issue #203's original ask) now that
    # #30 (endpoint hostname) is fixed. Live-tested with the gateway ON: every
    # investigation failed closed with "403 Egress request is not authorized...
    # unregistered in the Agent Registry" on the FIRST _sanitize() call, even
    # after (a) confirming the Model Armor endpoint is genuinely registered
    # with the correct URL, and (b) fixing a real, separate bug found along the
    # way -- the registered protocol_binding was JSONRPC but
    # modelarmor_v1.ModelArmorClient.get_transport_class() is actually GRPC
    # (fixed in agent_registry.tf regardless, since it was wrong either way).
    # Neither fix resolved the 403. Research points to a real product
    # limitation, not a config mistake: per Model Armor's own Agent Gateway
    # integration docs, direct API calls from protected agent code bypass the
    # gateway's supported egress integrations (MCP/OpenAI-format/A2A via
    # CONTENT_AUTHZ) entirely -- a plain google-cloud-modelarmor client call is
    # not one of them. A candidate fix (granting the agent identity
    # roles/modelarmor.calloutUser, the role Model Armor's own docs list for
    # gateway-side callers) needs a project IAM change outside this session's
    # pre-approved scope -- flagged to the user rather than applied blind.
    # Reverted the ENABLEMENT only; the corrected protocol_binding and the
    # HIGH confidence default both stay, since both are correct independent of
    # this blocker.
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
      min_instances = 2
      # Section 9 (2026-09-08): previously unset anywhere in this repo's Terraform --
      # docs/architecture/agent-engine.md's own audit called this out explicitly:
      # "max_instances is not set anywhere... status: UNKNOWN, the platform default
      # applies" (100, per Google's own docs, confirmed via WebSearch 2026-09-08:
      # https://docs.cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/deploy).
      # 10 is a deliberate, explicit ceiling for this Phase 1 rollout -- NOT derived
      # from a real concurrent load test (none has been run; see
      # docs/governance/capacity.md), a conservative starting cap chosen to bound
      # cost/blast-radius while still allowing real headroom above the 2 warm
      # instances. This is the "simplest supported runtime mechanism" the section
      # asked for -- the platform's own admission control, not a custom in-app
      # semaphore (explicitly warned against: "a per-process limiter is not a
      # global quota controller" -- a per-process limiter couldn't cap TOTAL
      # concurrency across multiple instances anyway). When this cap is hit, Vertex
      # AI Agent Engine's own platform layer returns a standard retryable error to
      # the caller before this container's code ever runs -- no application code
      # change needed for that half of the requirement.
      max_instances   = 10
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
