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

variable "authz_fail_open" {
  description = "When true, the gateway ALLOWS a request if the Model Armor authorization extension is unreachable (safe for rollout). Set false in production to fail closed. Only used when enable_agent_gateway = true."
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

variable "agent_gateway_subnet_cidr" {
  description = "Dedicated subnet CIDR for the Agent Gateway PSC-Interface network attachment. Must be at least a /28, RFC1918, and must NOT overlap 10.0.0.0/24, 10.0.1.0/24, or 10.0.2.0/24 (Agent Gateway egress-range exclusions). Only used when enable_agent_gateway = true."
  type        = string
  default     = "10.20.0.0/28"
}

# ============================================================================
# MODEL / MODEL ARMOR
# ============================================================================

variable "gemini_model" {
  description = "Gemini model the agent uses for reasoning."
  type        = string
  default     = "gemini-2.5-flash"
}

variable "model_endpoint_location" {
  description = "Vertex AI model-endpoint location for the agent's Gemini calls (GOOGLE_CLOUD_LOCATION). Defaults to \"global\" to match the Agent Gateway codelab (deploy_agent.py --model-endpoint-location). The regional/mTLS Vertex endpoint is not compatible with the gateway's TLS inspection; \"global\" is the codelab's proven path."
  type        = string
  default     = "global"
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
