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
  }))
  default = {}

  # Cross-variable validation (Terraform >= 1.9 — see versions.tf) — this
  # actually fails plan/apply, unlike a `check` block. trimspace() on both
  # sides so a whitespace-padded key (e.g. " sre-test-cluster") can't sneak
  # past the comparison and then silently collapse with the real entry when
  # agent/mcp_client.py's registry parser calls .strip() on every name.
  validation {
    condition = !contains(
      [for k in keys(var.additional_clusters) : trimspace(k)],
      trimspace(var.gke_cluster_name)
    )
    error_message = "additional_clusters contains a key that collides with var.gke_cluster_name (after trimming whitespace) — pick a distinct name for the additional cluster."
  }
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
  description = "When true, the gateway ALLOWS a request if the IAP authorization extension is unreachable (safe for rollout). Set false to fail closed / enforce. Only used when enable_agent_gateway = true."
  type        = bool
  default     = true
}

# ============================================================================
# NETWORKING
# ============================================================================

variable "agent_subnet_cidr" {
  description = "Primary subnet CIDR for the agent VPC in Project A."
  type        = string
  default     = "10.0.0.0/24"
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
  description = "Model Armor prompt-injection / jailbreak detection confidence threshold."
  type        = string
  default     = "MEDIUM_AND_ABOVE"

  validation {
    condition     = contains(["LOW_AND_ABOVE", "MEDIUM_AND_ABOVE", "HIGH"], var.model_armor_pi_confidence)
    error_message = "model_armor_pi_confidence must be LOW_AND_ABOVE, MEDIUM_AND_ABOVE, or HIGH."
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
