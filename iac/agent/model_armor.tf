# Model Armor templates — the safety layer for LLM inputs and outputs.
#
# Split into a REQUEST-side and a RESPONSE-side template to match the working
# Agent Gateway codelab (its gateway Model Armor extension references two distinct
# templates: agw-request-template / agw-response-template). The gateway's
# CONTENT_AUTHZ extension (agent_gateway.tf) passes request_template_id =
# sre_agent_request and response_template_id = sre_agent_response.
#
# Used two ways:
#   1. App layer (gateway OFF) — the agent code calls sanitize_user_prompt /
#      sanitize_model_response (agent/main.py); it uses the request template.
#   2. Gateway layer (gateway ON) — the CONTENT_AUTHZ authz extension inspects
#      traffic at the Agent Gateway. Defense in depth.

# Request-side: prompt injection / jailbreak + malicious URI + RAI. SRE agents
# ingest raw k8s logs, so input inspection is the high-risk path.
resource "google_model_armor_template" "sre_agent_request" {
  provider    = google-beta
  project     = var.project_a_id
  location    = var.region
  template_id = "sre-agent-request-guard"

  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = var.model_armor_pi_confidence
    }
    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }
    rai_settings {
      rai_filters {
        filter_type      = "SEXUALLY_EXPLICIT"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "HATE_SPEECH"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
    }
  }

  # 2026-08-24: explicit INSPECT_ONLY -- confirmed via the live REST API and
  # Google's own docs that leaving this unset does NOT mean inspect-only; it
  # defaults to ENFORCEMENT_TYPE_UNSPECIFIED, which is "Same as
  # INSPECT_AND_BLOCK". This template had no enforcement_type set at all
  # before this change, meaning it was silently blocking-capable the whole
  # time -- including during tonight's gateway CONTENT_AUTHZ trial, contrary
  # to PRODUCTION-LAUNCH-PLAN.md's explicit "no INSPECT_AND_BLOCK in Phase 1"
  # requirement. Do not remove this until the Phase 1 decision gate is
  # actually reached and blocking is deliberately approved.
  #
  # Briefly flipped to INSPECT_AND_BLOCK for one diagnostic test (2026-08-24,
  # 19:12 UTC) to check whether the PI/jailbreak filter detects a deliberate
  # prompt-injection payload -- it did not (0 DENIED results in the gateway
  # log across 2 real Gemini calls carrying the payload; the request's own
  # 500 error was unrelated to Model Armor). Reverted back to INSPECT_ONLY
  # immediately after, verified live via REST.
  template_metadata {
    enforcement_type = "INSPECT_ONLY"
  }

  depends_on = [google_project_service.apis]
}

# Response-side: RAI + malicious URI + Sensitive Data Protection (detect PII —
# IPs, emails — in the model's generated output before it leaves the gateway).
resource "google_model_armor_template" "sre_agent_response" {
  provider    = google-beta
  project     = var.project_a_id
  location    = var.region
  template_id = "sre-agent-response-guard"

  filter_config {
    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }
    rai_settings {
      rai_filters {
        filter_type      = "SEXUALLY_EXPLICIT"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "HATE_SPEECH"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
    }
    sdp_settings {
      basic_config {
        filter_enforcement = "ENABLED"
      }
    }
  }

  # See sre_agent_request's identical comment above -- same fix, same reason.
  template_metadata {
    enforcement_type = "INSPECT_ONLY"
  }

  depends_on = [google_project_service.apis]
}
