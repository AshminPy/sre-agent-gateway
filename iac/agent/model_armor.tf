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

# ── Project-level Model Armor floor setting — Terraform adoption of live drift ──
#
# 2026-08-25: this resource has existed live since 2026-07-13 (created by an
# earlier version of this same Terraform, commits 815392e/6b393fd/d78bdc2) but
# was removed from code in 9799494 on the theory that it caused the same
# gateway TLS man-in-the-middle problem as Agent Gateway CONTENT_AUTHZ. That
# reason does not hold: floor settings are a project-level Model Armor /
# Vertex AI / GKE Remote MCP integration setting, unrelated to the Agent
# Gateway's own traffic path — verified live 2026-08-24/25 by running a real
# investigation (real mTLS Gemini calls through the gateway) with floor-setting
# enforcement temporarily on; no TLS failure, no regression. The object was
# never destroyed, only orphaned from Terraform state — this block adopts it
# back under Terraform with NO functional change (see the import step in the
# PR that added this comment for the verified zero-diff proof).
#
# `enable_floor_setting_enforcement = true`: adopted + enabled via PR #183
# (2026-08-24/25) — verified zero-diff import, then a clean isolated flip,
# merged and applied via CI. Both services stay `inspect_only = true` — real
# inspection and logging, never blocking. Live-tested with real investigations
# and a real detector test (Google's own Safe Browsing test URIs +
# a prompt-injection payload): malicious_uris caught both test URIs with
# precise offsets every time; pi_and_jailbreak caught the payload in a short
# prompt but missed it in a longer one — evidence in docs/management/
# floor-settings-production-plan-2026-08-25.md.
#
# 2026-08-25: added `sdp_settings.basic_config` — Sensitive Data Protection,
# same fixed six-info-type basic config already used on the
# `sre_agent_response` template above (no new Cloud DLP template dependency).
# This project's evidence path pulls raw Kubernetes events/logs, which can
# carry real secrets or PII — this was the one gap left after PR #183.
# Stays `inspect_only` throughout; gated on its own synthetic SDP test before
# being called done (see the same plan doc).
resource "google_model_armor_floorsetting" "mcp" {
  count    = var.enable_agent_gateway ? 1 : 0
  provider = google-beta

  parent   = "projects/${var.project_a_id}"
  location = "global"

  enable_floor_setting_enforcement = true
  integrated_services              = ["GOOGLE_MCP_SERVER", "AI_PLATFORM"]

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
    sdp_settings {
      basic_config {
        filter_enforcement = "ENABLED"
      }
    }
  }

  # 2026-08-25: block-mode diagnostic run and reverted, same session (see
  # PR #190 for the temporary flip). Real result: a known-malicious payload
  # (Google's own Safe Browsing test URI) got sanitizationVerdict=BLOCK,
  # confirmed via the live SanitizeOperationLogEntry log (1 entry, BLOCK,
  # MATCH_FOUND) -- genuine blocking, not just logging. Immediately after, a
  # normal benign investigation completed normally with 44/44 log entries
  # verdict=ALLOW -- zero false-positive blocking of legitimate SRE traffic.
  # Back to inspect_only here -- production stays watch-only until a
  # deliberate decision to enable blocking for real.
  google_mcp_server_floor_setting {
    inspect_only         = true
    enable_cloud_logging = true
  }

  ai_platform_floor_setting {
    inspect_only         = true
    enable_cloud_logging = true
  }

  depends_on = [google_project_service.apis]
}
