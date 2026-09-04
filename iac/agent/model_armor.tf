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

  # 2026-08-25: inspect_and_block enabled for real (deliberate decision, not
  # a diagnostic this time). Diagnostic run earlier the same session (PR #190,
  # reverted by PR #191) proved: a known-malicious payload (Google's own Safe
  # Browsing test URI) got sanitizationVerdict=BLOCK, confirmed via the live
  # SanitizeOperationLogEntry log (1 entry, BLOCK, MATCH_FOUND) -- genuine
  # blocking, not just logging. Immediately after, a normal benign
  # investigation completed normally with 44/44 log entries verdict=ALLOW --
  # zero false-positive blocking of legitimate SRE traffic.
  #
  # Known limitation of that evidence, stated plainly: the benign test only
  # covered one investigation scenario (imagepull) and the malicious test only
  # covered the malicious_uris filter (its most reliable trigger, 8/8 hit
  # rate all session). pi_and_jailbreak has shown inconsistent detection
  # (catches a short prompt, misses the same payload in a longer one) and has
  # not been block-tested specifically; SDP has not been block-tested at all.
  # Blocking is now live for every filter, not just the one tested.
  #
  # ── 2026-08-26: REVERTED TO INSPECT-ONLY. The caveat above came true. ──
  #
  # The pi_and_jailbreak filter false-positived twice on ordinary SRE content,
  # both confirmed in SanitizeOperationLogEntry with every other filter
  # returning NO_MATCH_FOUND:
  #
  #   21:13:14  SANITIZE_MODEL_RESPONSE  MATCH_FOUND  MEDIUM_AND_ABOVE
  #       A describe_k8s_resource response -- an ordinary pod spec. The real
  #       body was discarded and replaced with Model Armor's notice in-band
  #       over HTTP 200. The agent then had no pod spec, and fabricated an
  #       image name, an error string and a root cause. Run
  #       run_20260826_211251_rlwe. See PR #199 for the code fix.
  #
  #   21:48:11  SANITIZE_USER_PROMPT     MATCH_FOUND  MEDIUM_AND_ABOVE
  #       The agent's OWN static system prompt: "You are an SRE evidence
  #       analyst. Extract the most investigation-relevant facts from tool
  #       output. Focus on: failures, errors, restart counts, exit codes, OOM
  #       kills, image pull errors, scheduling failures, warning events."
  #       Nothing malicious in it. Blocking it left Gemini with no response
  #       text and crashed the run.
  #
  # Google's own guidance is to start with inspect-only, read the logs, then
  # enable blocking once the false-positive rate is known:
  # https://docs.cloud.google.com/model-armor/configure-floor-settings
  # We went straight to blocking on 2026-08-25 without that measurement step.
  # This restores the missing step -- detection and Cloud Logging stay fully
  # on, nothing is discarded or rewritten.
  #
  # BEFORE RE-ENABLING inspect_and_block, all three must hold:
  #   1. A week of MATCH_FOUND entries reviewed with zero false positives on
  #      real SRE traffic (k8s specs, events, logs, and the agent's own
  #      prompts).
  #   2. pi_and_jailbreak block-tested specifically, with a real payload.
  #   3. SDP block-tested at all -- still never done.
  #
  # 2026-08-27: PR #200's own apply failed twice, on two different errors, in
  # the same evening -- both confirmed against the real API/provider, neither
  # guessed:
  #
  # Attempt 1 (PR #200 as merged): only inspect_and_block=false was set.
  #   Error 400: "Enforcement type must be specified for integrated
  #   service(s): 'GOOGLE_MCP_SERVER, AI_PLATFORM'." reason:
  #   "ENFORCEMENT_TYPE_MISSING". Leaving the enforcement type fully unset is
  #   ambiguous and the API refuses it.
  #
  # Attempt 2 (first fix here): tried setting BOTH inspect_and_block=false AND
  #   inspect_only=true, from a provider-schema dump that showed both fields
  #   as independently optional. `terraform plan` itself rejected it:
  #   "only one of `inspect_and_block,inspect_only` can be specified, but ...
  #   were specified." The schema dump showed each field's own optionality,
  #   not the ExactlyOneOf constraint between them -- wrong evidence to read
  #   for this question.
  #
  # Correct form: inspect_only=true ALONE. inspect_and_block is not "the
  # other value of the same switch" -- omitting it is how you select
  # inspect-only, not setting it to false.
  # TEMPORARY — 2026-09-04 controlled A/B comparison test (see docs/management/
  # model-armor-ab-comparison-2026-09-04.md). Reverted to inspect_only=true
  # immediately after Phase B's scenarios run, same commit-revert pattern as the
  # 2026-08-25/26 attempt. Do not leave this merged.
  google_mcp_server_floor_setting {
    inspect_and_block    = true
    enable_cloud_logging = true
  }

  # Same revert, same reason. This is the one that blocked the agent's own
  # evidence-analyst system prompt at 21:48:11 and crashed the run -- the
  # AI_PLATFORM integration covers the model's prompts and responses, so a
  # false positive here is fatal rather than merely lossy.
  #
  # These two blocks are independent knobs and could be set differently, but
  # both filters that misfired were pi_and_jailbreak at MEDIUM_AND_ABOVE, and
  # both directions carry ordinary SRE text. Blocking one and not the other
  # would only move the failure. Re-enable both together, under the same three
  # conditions listed above.
  # Same fix, same reason -- see the block above.
  # TEMPORARY — same 2026-09-04 test, see comment on the block above.
  ai_platform_floor_setting {
    inspect_and_block    = true
    enable_cloud_logging = true
  }

  depends_on = [google_project_service.apis]
}
