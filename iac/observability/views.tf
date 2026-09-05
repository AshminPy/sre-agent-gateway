# Normalized views — the actual contract Looker Studio queries. Never point
# a Looker Studio chart at a raw sink table directly; always go through one
# of these three.
#
# Raw table names below are NOT chosen by this Terraform — Cloud Logging
# derives them from each sink's log ID (dots/slashes/hyphens sanitized to
# underscores) and there is no override. Confirmed empirically against this
# project's real dataset after the sinks in investigation_dashboard.tf had
# processed real log entries (`bq ls sreagent-t2-demo:sre_agent_investigations`):
#   sre-agent-investigations                         -> sre_agent_investigations
#   modelarmor.googleapis.com/sanitize_operations     -> modelarmor_googleapis_com_sanitize_operations
#   networkservices.googleapis.com/gateway_requests   -> networkservices_googleapis_com_gateway_requests
# If a raw table is ever recreated under a different sanitized name (e.g. the
# log ID changes), these `FROM` clauses must be updated to match — Terraform
# has no way to detect that automatically since Cloud Logging manages the
# raw tables outside this stack's direct control.

# ── v_investigations — one row per investigation/run ───────────────────────
#
# Note on error_type/error: these two columns exist in application code
# (agent/main.py's crash-terminal-event, agent/nodes/rca_builder.py's
# completion event) but do NOT yet exist in the raw table's BigQuery schema —
# BigQuery's schema auto-detection only materializes a column once at least
# one log entry has a non-null value for it, and no crash has occurred since
# the fix was deployed (2026-09-05). This is disclosed, not hidden: querying
# them here would fail CREATE VIEW today. See docs/dashboard/schema.md's "how
# to add another field" section for the exact one-line addition once the
# first real crash-terminal event lands.
resource "google_bigquery_table" "v_investigations" {
  dataset_id          = google_bigquery_dataset.dashboard.dataset_id
  project             = var.project_a_id
  table_id            = "v_investigations"
  deletion_protection = false

  view {
    use_legacy_sql = false
    query          = <<-SQL
      SELECT
        jsonPayload.run_id                             AS run_id,
        timestamp                                       AS event_timestamp,
        jsonPayload.event_type                          AS event_type,
        jsonPayload.terminal_kind                       AS terminal_kind,
        jsonPayload.terminal_event_schema_version       AS terminal_event_schema_version,
        jsonPayload.status                              AS status,
        jsonPayload.environment                         AS environment,
        jsonPayload.cluster                             AS cluster,
        jsonPayload.cluster_region                      AS cluster_region,
        -- Deterministic derivation, not a guess: mcp_router.py selects
        -- gke_remote_mcp iff cluster_type=="gke", k8s_mcp otherwise — the
        -- only two values MCP_REGISTRY ever contains (verified against
        -- agent/mcp_client.py and agent/nodes/mcp_router.py source, 2026-09-05).
        CASE
          WHEN jsonPayload.mcp_source = "gke_remote_mcp" THEN "GKE"
          WHEN jsonPayload.mcp_source = "k8s_mcp"         THEN "non-GKE"
          ELSE NULL
        END                                              AS cluster_type,
        jsonPayload.mcp_source                          AS mcp_source,
        jsonPayload.cluster_routing_method               AS cluster_routing_method,
        jsonPayload.cluster_routing_reason               AS cluster_routing_reason,
        jsonPayload.model_name                          AS model,
        jsonPayload.tokens_input                        AS tokens_input,
        jsonPayload.tokens_output                       AS tokens_output,
        jsonPayload.tokens_total                        AS tokens_total,
        -- Canonical cost field per explicit decision: reuse the agent's own
        -- estimated_cost_usd, verified 2026-09-05 against Google's live
        -- official Gemini 2.5 Pro pricing (exact match) — no second,
        -- independently-computed cost exists anywhere in this stack.
        jsonPayload.estimated_cost_usd                  AS estimated_llm_cost_usd,
        ARRAY_LENGTH(jsonPayload.tools_called)          AS tool_call_count,
        jsonPayload.failed_tools_count                  AS failed_tool_count,
        ARRAY_LENGTH(jsonPayload.evidence_ids)          AS evidence_count,
        jsonPayload.investigation_completeness_score     AS investigation_completeness_score,
        jsonPayload.root_cause_confidence_score          AS root_cause_confidence_score,
        -- Raw, unmodified Agent classification — never discarded.
        jsonPayload.incident_type                       AS incident_type_raw,
        -- Best-effort normalization for charting ONLY. This is NOT an
        -- authoritative Agent classification — incident_type is free-text
        -- LLM output with a suggested-but-unenforced set (agent/prompts.py),
        -- not a validated enum. Do not present rca_category_normalized as
        -- ground truth; use incident_type_raw for anything that needs the
        -- Agent's actual, unmodified output.
        CASE
          WHEN REGEXP_CONTAINS(IFNULL(jsonPayload.incident_type, ""), r"(?i)imagepull")     THEN "ImagePullBackOff"
          WHEN REGEXP_CONTAINS(IFNULL(jsonPayload.incident_type, ""), r"(?i)crashloop")      THEN "CrashLoopBackOff"
          WHEN REGEXP_CONTAINS(IFNULL(jsonPayload.incident_type, ""), r"(?i)oom")            THEN "OOMKilled"
          WHEN REGEXP_CONTAINS(IFNULL(jsonPayload.incident_type, ""), r"(?i)configmap|config") THEN "Missing ConfigMap"
          WHEN REGEXP_CONTAINS(IFNULL(jsonPayload.incident_type, ""), r"(?i)dns|network")    THEN "Network/DNS"
          WHEN REGEXP_CONTAINS(IFNULL(jsonPayload.incident_type, ""), r"(?i)healthy|false")  THEN "Healthy / False Alarm"
          -- Deliberately NOT mapping outcome="insufficient_evidence" to this
          -- bucket: that outcome means "could not reach a confident
          -- conclusion," which can happen because the pod was genuinely
          -- healthy OR because tool failures/errors prevented evidence
          -- collection (a real run hit exactly this — status="error",
          -- 2/5 tool calls failed, yet outcome="insufficient_evidence").
          -- Conflating the two would misreport a failed run as a healthy one.
          ELSE "Other"
        END                                              AS rca_category_normalized,
        jsonPayload.outcome                             AS outcome,
        jsonPayload.confidence_band                     AS confidence_band,
        jsonPayload.loop_exit_reason                    AS loop_exit_reason,
        jsonPayload.model_latency_s                     AS model_latency_s,
        jsonPayload.mcp_latency_s                       AS mcp_latency_s,
        jsonPayload.total_latency_s                     AS total_latency_s,
        jsonPayload.trace_id                            AS trace_id,
        jsonPayload.namespace                           AS namespace,
        jsonPayload.pod                                 AS pod,
        jsonPayload.gcs_evidence_path                   AS gcs_evidence_path,
        -- Direct log links, built here (not in application code) — no
        -- run_id-scoped URL exists for the Agent's own log until we add one;
        -- this is the best currently-possible link: pre-filtered to the
        -- exact log + a query on this exact run_id.
        CONCAT(
          "https://console.cloud.google.com/logs/query;query=",
          "logName%3D%22projects%2F", "${var.project_a_id}", "%2Flogs%2Fsre-agent-investigations%22%0AjsonPayload.run_id%3D%22",
          jsonPayload.run_id,
          "%22;project=", "${var.project_a_id}"
        )                                                AS agent_log_link
      FROM `${var.project_a_id}.${google_bigquery_dataset.dashboard.dataset_id}.sre_agent_investigations`
      WHERE jsonPayload.event_type = "sre_agent_run_terminal"
    SQL
  }

  depends_on = [google_logging_project_sink.investigations]
}

# ── v_model_armor_activity — aggregate/time-series only, NO run_id join ────
#
# Covers all 3 Model Armor mechanisms (see docs/dashboard/schema.md), never
# conflated into one status. "mechanism" here is a best-effort classification
# by template_id, with one disclosed ambiguity: the app-level _sanitize()
# path and the CONTENT_AUTHZ gateway extension share the SAME templates
# (sre-agent-request-guard/sre-agent-response-guard) and cannot be told apart
# by template_id alone. App-level is currently disabled by configuration
# (MODEL_ARMOR_TEMPLATE only set when the gateway is off, and the gateway is
# on) — so in the CURRENT deployment, any entry under those two templates is
# effectively the gateway CONTENT_AUTHZ path, but this view does not assume
# that will always remain true.
resource "google_bigquery_table" "v_model_armor_activity" {
  dataset_id          = google_bigquery_dataset.dashboard.dataset_id
  project             = var.project_a_id
  table_id            = "v_model_armor_activity"
  deletion_protection = false

  view {
    use_legacy_sql = false
    query          = <<-SQL
      SELECT
        timestamp                                                             AS event_timestamp,
        resource.labels.template_id                                          AS template_id,
        CASE
          WHEN STARTS_WITH(resource.labels.template_id, "FLOOR_SETTING")     THEN "floor_setting"
          WHEN resource.labels.template_id IN ("sre-agent-request-guard", "sre-agent-response-guard")
                                                                               THEN "app_or_gateway_content_authz"
          ELSE "unknown"
        END                                                                    AS mechanism,
        labels.modelarmor_googleapis_com_client_name                         AS client_name,
        jsonpayload_v1_sanitizeoperationlogentry.operationtype                AS operation_type,
        jsonpayload_v1_sanitizeoperationlogentry.sanitizationresult.filtermatchstate
                                                                               AS filter_match_state,
        jsonpayload_v1_sanitizeoperationlogentry.sanitizationresult.sanitizationverdict
                                                                               AS sanitization_verdict,
        jsonpayload_v1_sanitizeoperationlogentry.sanitizationresult.invocationresult
                                                                               AS invocation_result,
        -- Dashboard state, kept distinct from the platform limitation:
        -- "unsupported" below is NOT derivable from this log at all (the
        -- absence of a log entry under sre-agent-request-guard/response-guard
        -- for a real response IS the unsupported signal, per PHASE1_EVIDENCE_LOG.md
        -- — a negative, not something this row-level view can show). This
        -- column only ever reports allowed/detected/blocked/error for entries
        -- that DID get inspected.
        CASE
          WHEN jsonpayload_v1_sanitizeoperationlogentry.sanitizationresult.invocationresult != "SUCCESS" THEN "error"
          WHEN jsonpayload_v1_sanitizeoperationlogentry.sanitizationresult.sanitizationverdict LIKE "%BLOCK%" THEN "blocked"
          WHEN jsonpayload_v1_sanitizeoperationlogentry.sanitizationresult.filtermatchstate = "MATCH_FOUND" THEN "detected"
          ELSE "allowed"
        END                                                                    AS dashboard_state
      FROM `${var.project_a_id}.${google_bigquery_dataset.dashboard.dataset_id}.modelarmor_googleapis_com_sanitize_operations`
    SQL
  }

  depends_on = [google_logging_project_sink.model_armor_activity]
}

# ── v_gateway_activity — aggregate/time-series only, NO run_id join ────────
#
# ALLOW/DENY trends for the Reliability & Security page. No per-investigation
# correlation is possible (confirmed, Phase A telemetry gap analysis,
# 2026-09-05) — the raw log carries no run_id/trace_id at all. Timestamp +
# hostname is the only available correlation, and this view does not attempt
# to fake a per-run join.
resource "google_bigquery_table" "v_gateway_activity" {
  dataset_id          = google_bigquery_dataset.dashboard.dataset_id
  project             = var.project_a_id
  table_id            = "v_gateway_activity"
  deletion_protection = false

  view {
    use_legacy_sql = false
    query          = <<-SQL
      SELECT
        timestamp                                                                          AS event_timestamp,
        jsonpayload_type_loadbalancerlogentry.enforcedgatewaysecuritypolicy.hostname        AS destination_hostname,
        jsonpayload_type_loadbalancerlogentry.authzpolicyinfo.result                        AS overall_result,
        policy.name                                                                         AS policy_name,
        policy.result                                                                       AS policy_result
      FROM `${var.project_a_id}.${google_bigquery_dataset.dashboard.dataset_id}.networkservices_googleapis_com_gateway_requests`,
      UNNEST(jsonpayload_type_loadbalancerlogentry.authzpolicyinfo.policies) AS policy
    SQL
  }

  depends_on = [google_logging_project_sink.gateway_activity]
}
