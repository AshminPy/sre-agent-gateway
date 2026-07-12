# ============================================================================
# REQUIRED
# ============================================================================

variable "project_a_id" {
  description = "Project A (where the agent runs). Used to derive the agent principalSet that receives cross-project read access here in Project B."
  type        = string
}

variable "project_b_id" {
  description = "Project B — hosts the GKE cluster the agent investigates."
  type        = string
}

# ============================================================================
# GENERAL
# ============================================================================

variable "region" {
  description = "Region for the GKE cluster and regional resources."
  type        = string
  default     = "us-central1"
}

# ============================================================================
# GKE CLUSTER — create a demo cluster, or point at an existing one
# ============================================================================

variable "create_gke_cluster" {
  description = "When true, create a demo GKE Autopilot cluster in Project B. Set false to use a cluster you already have (the cross-project grants are still applied so the agent can read it)."
  type        = bool
  default     = true
}

variable "gke_cluster_name" {
  description = "Name of the GKE cluster. If create_gke_cluster = true this cluster is created; otherwise it must already exist in Project B."
  type        = string
  default     = "sre-test-cluster"
}

# ============================================================================
# CROSS-PROJECT ACCESS
# ============================================================================

variable "deployer_sa_email" {
  description = "Email of the CI/CD deployer SA from the agent stack (terraform -chdir=iac/agent output -raw deployer_sa_email). Granted the minimal roles needed to manage this stack from CI. Leave empty to skip (e.g. when applying locally with your own credentials)."
  type        = string
  default     = ""
}

variable "custom_mcp_runtime_sa_email" {
  description = "Email of the custom Cloud Run MCP runtime SA from the agent stack (only if enable_custom_mcp was true there). When set, it is granted read-only GKE access in Project B so the fallback MCP can reach the cluster. Leave empty if unused."
  type        = string
  default     = ""
}
