# ============================================================================
# REQUIRED — set these in terraform.tfvars
# ============================================================================

variable "project_a_id" {
  description = "GCP project ID that hosts the agent, Agent Gateway, Model Armor, Memory Bank, buckets, and monitoring. Must already exist with billing linked."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_a_id))
    error_message = "project_a_id must be a valid GCP project ID (6-30 chars, lowercase letters/digits/hyphens)."
  }
}

variable "project_b_id" {
  description = "GCP project ID that hosts the GKE cluster the agent investigates (deployed by the iac/gke-access stack). The agent targets clusters here cross-project via GKE Remote MCP."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_b_id))
    error_message = "project_b_id must be a valid GCP project ID."
  }
}

variable "gke_cluster_name" {
  description = "Name of the GKE cluster in Project B that the agent investigates by default. Written into clusters.json so the agent can resolve it."
  type        = string
  default     = "sre-test-cluster"
}

# Extra clusters beyond the default one above, keyed by canonical cluster name
# (this becomes each entry's "name" field in clusters.json — must be unique and
# must not collide with var.gke_cluster_name; enforced below by a real
# validation block, not a `check` block — `check` blocks only ever emit a
# warning, they never fail plan/apply, so one was tried here and rejected in
# review before this fix landed). Terraform is the sole source of truth for
# clusters.json — there is no lifecycle.ignore_changes on the bucket object,
# so hand-edits to the file in GCS are NOT supported and will be overwritten
# on the next apply. Add clusters here instead. Defaults to empty so existing
# single-cluster deployments are unaffected.
variable "additional_clusters" {
  description = "Extra clusters (beyond var.gke_cluster_name) the agent can investigate, keyed by canonical cluster name. Merged into clusters.json alongside the default cluster."
  type = map(object({
    aliases            = optional(list(string), [])
    project            = string
    region             = string
    type               = optional(string, "gke")
    environment        = optional(string, "production")
    allowed_namespaces = optional(list(string), [])
    owner              = optional(string, "")
    enabled            = optional(bool, true)
    # Section 5 redesign: the shared custom MCP (mcp/server.py) now serves
    # every "custom"-type cluster from ONE Cloud Run service, keyed by this
    # field -- not a separate deployment per cluster. Only meaningful for
    # type="custom" entries reached via GKE Fleet Connect Gateway; a "gke"
    # entry goes through GKE Remote MCP instead and never reads this field.
    # LEGACY path: requires the matching context to already exist in the
    # static, image-baked mcp/connect-gateway-kubeconfig.yaml -- adding a
    # cluster here needs an image rebuild too. Prefer fleet_project_number
    # below for any NEW on-prem cluster; this field is kept only so the
    # already-live-validated sre-lab entry is never forced to migrate.
    kube_context = optional(string, "")
    # Dynamic Connect Gateway (added 2026-09-07): when set (together with
    # fleet_membership, which defaults to this map key if left empty), the
    # custom MCP builds the Connect Gateway connection at request time from
    # these two values -- no static kubeconfig file, no image rebuild. This
    # is the genuinely plug-and-play path: register the cluster in the Fleet,
    # grant RBAC, add this one registry entry, apply -- done. Find the
    # project number with `gcloud projects describe <project-id>
    # --format='value(projectNumber)'`.
    fleet_project_number = optional(string, "")
    fleet_membership     = optional(string, "")
  }))
  # Phase 1: sre-lab (local kind cluster standing in for on-prem, registered
  # into the GCP fleet — see iac/agent/onprem_fleet.tf and
  # docs/connect-gateway-onprem.md) is a real, ongoing cluster this agent
  # investigates via Connect Gateway, not a throwaway test fixture — it needs
  # to survive every CI-driven apply, and CI does not pass a -var override for
  # this variable, so it lives in the default rather than only in the
  # (gitignored) local terraform.tfvars. A different deployment of this module
  # overrides it via its own tfvars, same as every other var here.
  default = {
    "sre-lab" = {
      aliases            = ["kind-sre-lab", "on-prem-lab", "connect-gateway-lab"]
      project            = "sreagent-t2-demo"
      region             = "global"
      type               = "custom"
      environment        = "test"
      allowed_namespaces = ["test-incidents"]
      owner              = "sre-platform"
      enabled            = true
      # Preserves exactly the value previously passed as the single global
      # var.custom_mcp_kube_context env var -- migrating this cluster from
      # "the only cluster this MCP service knows about" to "one registry
      # entry among possibly many" changes nothing about how it connects.
      kube_context = "connectgateway_sreagent-t2-demo_global_sre-lab"
    }
  }

  # 2026-08-26: the collision guard MOVED out of this variable, into a
  # `lifecycle.precondition` on google_storage_bucket_object.clusters_json
  # (buckets.tf). It was a cross-variable `validation` block referencing
  # var.gke_cluster_name, which requires Terraform >= 1.9. Company Spacelift
  # is pinned to 1.4.7, and this repo must stay deployable on the same version
  # as sre-agent-app-infra so the two cannot drift.
  #
  # Nothing is weakened by the move. A precondition (Terraform >= 1.2) fails
  # plan and apply exactly like a validation block does — unlike a `check`
  # block, which only ever warns and was rejected in review for that reason.
  # The condition itself is unchanged, trimspace() on both sides included.
}

variable "notification_email" {
  description = "Email address that receives Cloud Monitoring alerts (high error rate, high escalation rate, cost spike)."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.notification_email))
    error_message = "notification_email must be a valid email address."
  }
}

variable "github_repo" {
  description = "GitHub repo (owner/name) allowed to deploy via Workload Identity Federation, e.g. \"your-org/testing2-gcp-sre-agent\". Used to scope the CI deployer's WIF binding."
  type        = string
}

variable "tfstate_bucket" {
  description = "Name of the pre-created GCS bucket holding Terraform state. When set, the CI deployer SA is granted object access to it. Leave empty to manage that grant manually."
  type        = string
  default     = ""
}

# ============================================================================
# GENERAL — sensible defaults
# ============================================================================

variable "region" {
  description = "GCP region for all regional resources (agent, gateway, buckets, GKE)."
  type        = string
  default     = "us-central1"
}

# ============================================================================
# FEATURE FLAGS
# ============================================================================

variable "enable_agent_gateway" {
  description = "Deploy the Agent Gateway (egress governance) in front of the agent. When true, the agent's outbound traffic (model, MCP, telemetry) is routed through the gateway; you must also run the post-apply scripts (register endpoints, attach gateway to engine). Set false for a gateway-free deploy where the agent works immediately."
  type        = bool
  default     = true
}

variable "create_wif" {
  description = "Manage the CI/CD deployer identity (Workload Identity Federation pool/provider, deployer service account, and its role bindings) inside this stack. Set true for a self-contained local `terraform apply` that bootstraps everything. Set false when the deployer identity is created out-of-band by scripts/bootstrap_wif.sh — required for the git-driven flow, since GitHub Actions must authenticate AS that identity to run the apply (a deployer cannot create the very identity it runs as). CI passes create_wif=false automatically."
  type        = bool
  default     = true
}

variable "enable_custom_mcp" {
  description = "Deploy the custom Cloud Run MCP server as a FALLBACK to GKE Remote MCP. Optional — the agent works with GKE Remote MCP alone. When true you must build & push the MCP image first (see README / `make build-mcp`); until then the service runs a Google placeholder image."
  type        = bool
  default     = false
}

variable "custom_mcp_image" {
  description = "Container image for the custom Cloud Run MCP fallback. Defaults to a Google placeholder so the first apply succeeds; replace by building & pushing your image (see README) or set explicitly. Only used when enable_custom_mcp = true."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/placeholder"
}

variable "custom_mcp_kube_context" {
  description = "Kubeconfig context name the custom MCP's get_k8s_clients() should use for the Connect Gateway (non-GKE/on-prem) path, baked into the image at mcp/connect-gateway-kubeconfig.yaml. Empty (default) keeps the service on the direct-GKE-endpoint or local-kubeconfig branches — see mcp/server.py. Only used when enable_custom_mcp = true."
  type        = string
  default     = ""
}

variable "onprem_fleet_membership" {
  description = "GKE Fleet membership name for an already-onboarded non-GKE/on-prem cluster (e.g. the Phase 1 kind cluster 'sre-lab'). This is a plain reference, not an orchestrator — fleet registration/Connect Agent install is a manual, authorized-operator action (see docs/connect-gateway-onprem.md), and Kubernetes RBAC lives in the separate AshminPy/sre-k8s-rbac repo. Empty (default) skips granting the Google IAM binding in onprem_fleet.tf entirely."
  type        = string
  default     = ""
}

variable "iap_iam_enforcement_mode" {
  description = "Agent Gateway IAP REQUEST_AUTHZ mode. \"DRY_RUN\" logs allow/deny decisions without blocking; null enforces (blocks unauthorized egress). Defaults to enforce — validated live 2026-07-14: enforce mode passed the same end-to-end smoke test as DRY_RUN with zero behavior difference for legitimate traffic (fail_open=true still protects against IAP itself being unreachable). Set to \"DRY_RUN\" to go back to audit-only."
  type        = string
  default     = null

  validation {
    condition     = var.iap_iam_enforcement_mode == null || var.iap_iam_enforcement_mode == "DRY_RUN"
    error_message = "iap_iam_enforcement_mode must be \"DRY_RUN\" or null."
  }
}

variable "authz_fail_open" {
  description = "When true, the gateway ALLOWS a request if the IAP authorization extension is unreachable (safe for rollout). Set false to fail closed / enforce. Only used when enable_agent_gateway = true. Default changed true -> false 2026-09-05: normal REQUEST_AUTHZ/IAP enforcement was already proven (real revoke/retry test, no bypass, no stale enforcement); the remaining open question was specifically this extension-unreachable failure mode, which fail-open leaves silently permissive. CI never overrides this var (same reason model_armor_pi_confidence's default lives here, not in terraform.tfvars, which is gitignored) — changing the default here is what actually makes this durable through the real CI/CD pipeline."
  type        = bool
  default     = false
}

# ============================================================================
# MODEL / MODEL ARMOR
# ============================================================================

variable "gemini_model" {
  description = "Gemini model the agent uses for reasoning."
  type        = string
  default     = "gemini-2.5-flash"
}

# Cost-estimation pricing — the ONE place these numbers live. agent/gemini_client.py reads
# these via GEMINI_PRICE_INPUT/GEMINI_PRICE_OUTPUT and falls back to 0.0 (never a plausible-
# looking wrong number) if they're ever missing, so a misconfiguration shows up as an obvious
# "$0.00 cost" instead of a silently-wrong estimate. Defaults below are Gemini 2.5 Pro's real
# published rate for prompts <= 200K tokens (cloud.google.com/vertex-ai/generative-ai/pricing,
# verified 2026-08-10) — Pro's real pricing is tiered (>200K tokens costs more), which this
# single flat rate does NOT model; acceptable because this agent's investigations are
# hard-capped (5 loop steps, evidence compressed before every prompt) and never realistically
# approach 200K tokens in one call. Update BOTH values together if var.gemini_model changes to
# a different model with different pricing — nothing derives one from the other automatically.
variable "gemini_price_input_per_1m" {
  description = "USD price per 1M input tokens for var.gemini_model, <= 200K token context. Must match the deployed model's real published rate — see cloud.google.com/vertex-ai/generative-ai/pricing."
  type        = number
  default     = 1.25
}

variable "gemini_price_output_per_1m" {
  description = "USD price per 1M output tokens for var.gemini_model, <= 200K token context. Must match the deployed model's real published rate — see cloud.google.com/vertex-ai/generative-ai/pricing."
  type        = number
  default     = 10.0
}


variable "model_armor_pi_confidence" {
  description = "Model Armor prompt-injection / jailbreak detection confidence threshold. HIGH per the Phase 1 A/B/C comparison (2026-08/09): MEDIUM_AND_ABOVE false-positived on ordinary SRE text (7/208 test calls, matching issue #202's 3 real false positives in one evening); HIGH found the same real malicious payloads with 0/68 false positives. Raised from MEDIUM_AND_ABOVE specifically because issue #203 makes app-level Model Armor live for the first time -- a false-positive block here corrupts a real RCA summary, not just a tool call."
  type        = string
  default     = "HIGH"

  validation {
    condition     = contains(["LOW_AND_ABOVE", "MEDIUM_AND_ABOVE", "HIGH"], var.model_armor_pi_confidence)
    error_message = "model_armor_pi_confidence must be LOW_AND_ABOVE, MEDIUM_AND_ABOVE, or HIGH."
  }
}

# Token-budget cleanup (issue #63). MAX_TOKENS_PER_RUN already exists and is already
# enforced -- agent/nodes/loop_controller.py has read it from the environment (hard cap,
# default 100000) since before this change. Terraform never set it, so production has
# always silently run on that Python-side default. This makes Terraform the single source
# of truth for that already-live number, and adds a configurable early-warning ratio on
# top of it -- both variables below, never a static/guessed threshold in code.
variable "max_tokens_per_run" {
  description = "Hard cap on tokens consumed by a single investigation (agent/nodes/loop_controller.py's MAX_TOKENS_PER_RUN). Default matches the pre-existing Python-side fallback exactly, so setting this variable alone does not change current behavior. Set to 0 to disable the hard cap (mirrors loop_controller.py's own disable convention) -- this also disables the token_usage_warning alert in monitoring.tf, since a threshold derived from 0 would be meaningless."
  type        = number
  default     = 100000

  validation {
    condition     = var.max_tokens_per_run >= 0 && var.max_tokens_per_run == floor(var.max_tokens_per_run)
    error_message = "max_tokens_per_run must be a non-negative whole number (0 to disable, or a positive integer)."
  }
}

variable "token_warning_ratio" {
  description = "Fraction of max_tokens_per_run at which the token-usage warning alert (iac/agent/monitoring.tf) fires -- e.g. 0.8 warns at 80% of the hard cap, before loop_controller.py actually exits on token_budget_exceeded. Must be between 0 (exclusive) and 1 (inclusive) so the warning always fires at or before the hard cap itself."
  type        = number
  default     = 0.8

  validation {
    condition     = var.token_warning_ratio > 0 && var.token_warning_ratio <= 1
    error_message = "token_warning_ratio must be greater than 0 and at most 1."
  }
}

# ============================================================================
# MONITORING
# ============================================================================

variable "log_analytics_retention_days" {
  description = "Retention (days) for the _Default log bucket upgraded to Observability Analytics (needed for Agent Gateway console tabs)."
  type        = number
  default     = 30
}
