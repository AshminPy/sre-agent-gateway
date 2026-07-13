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

  depends_on = [google_project_service.apis]
}

# ── Project-level Model Armor FLOOR SETTING ────────────────────────────────
# The proven-working codelab project has a Model Armor floor setting integrated
# with GOOGLE_MCP_SERVER (verified live: enforcement=true, integratedServices=
# ['GOOGLE_MCP_SERVER']). This enables Model Armor's native integration with the
# Google MCP Server path that the Agent Gateway governs. Our t2 project had NO
# floor setting — the one live, project-level difference vs the working codelab.
# Only created when the gateway is enabled (the gateway is what inspects MCP).
resource "google_model_armor_floorsetting" "mcp" {
  count    = var.enable_agent_gateway ? 1 : 0
  provider = google-beta

  parent   = "projects/${var.project_a_id}"
  location = "global"

  enable_floor_setting_enforcement = true
  integrated_services              = ["GOOGLE_MCP_SERVER"]

  filter_config {
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
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = var.model_armor_pi_confidence
    }
    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }
  }

  # inspect_only (not inspect_and_block): Model Armor still inspects and logs
  # every governed MCP tool call/response (full governance visibility), but does
  # NOT block. Raw kubectl output (e.g. `describe pod`) trips the PI/jailbreak
  # filter as a false positive; inspect_and_block would drop legitimate SRE
  # evidence mid-investigation. Switch to inspect_and_block once the filter set
  # is tuned for infra payloads.
  google_mcp_server_floor_setting {
    inspect_only         = true
    enable_cloud_logging = true
  }

  depends_on = [google_project_service.apis]
}
