# Model Armor template — the safety layer for LLM inputs and outputs.
#
# Used two ways:
#   1. App layer  — the agent code calls sanitize_user_prompt / sanitize_model_response
#      (see agent/main.py). The runtime identity gets roles/modelarmor.user (iam.tf).
#   2. Gateway layer (optional) — a CONTENT_AUTHZ authz extension inspects traffic at
#      the Agent Gateway (see agent_gateway.tf). Defense in depth.

resource "google_model_armor_template" "sre_agent" {
  provider    = google-beta
  project     = var.project_a_id
  location    = var.region
  template_id = "sre-agent-guard"

  filter_config {
    # Prompt injection / jailbreak — SRE agents ingest raw k8s logs (high risk).
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = var.model_armor_pi_confidence
    }

    # Malicious URLs in logs, events, or environment variables.
    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }

    # Responsible-AI filters on agent output.
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

    # Sensitive Data Protection — detect PII (IPs, emails) in logs/responses.
    sdp_settings {
      basic_config {
        filter_enforcement = "ENABLED"
      }
    }
  }

  depends_on = [google_project_service.apis]
}
