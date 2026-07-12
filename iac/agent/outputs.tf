# Outputs consumers actually need: to configure the local CLI/.env, to run the
# post-apply scripts, and to feed the iac/gke-access stack.

output "project_a_id" {
  description = "Project A (agent + gateway)."
  value       = var.project_a_id
}

output "project_a_number" {
  description = "Project A number — needed by the iac/gke-access stack to build the agent principalSet."
  value       = data.google_project.a.number
}

output "region" {
  description = "Deployment region."
  value       = var.region
}

output "reasoning_engine_id" {
  description = "Numeric ID of the SRE agent reasoning engine (for invoke_agent.py / REST calls)."
  value       = local.engine_numeric_id
}

output "reasoning_engine_resource_name" {
  description = "Full resource name of the SRE agent reasoning engine."
  value       = google_vertex_ai_reasoning_engine.sre_agent.id
}

output "memory_bank_resource_name" {
  description = "Full resource name of the Memory Bank reasoning engine."
  value       = google_vertex_ai_reasoning_engine.memory_bank.id
}

output "agent_identity_member" {
  description = "The runtime Agent Identity principal (this specific agent)."
  value       = local.agent_identity_member
}

output "agent_identity_principal_set" {
  description = "Org principalSet covering all Agent Engine agents in Project A. Use this in the iac/gke-access stack's tfvars for the cross-project GKE grants."
  value       = local.agent_identity_principal_set
}

output "agent_gateway_id" {
  description = "Agent Gateway resource ID (null when enable_agent_gateway = false). Needed by scripts/attach_gateway_to_engine.sh."
  value       = var.enable_agent_gateway ? google_network_services_agent_gateway.sre_egress[0].id : null
}

output "evidence_bucket_name" {
  description = "GCS bucket for RCA evidence + run output."
  value       = google_storage_bucket.evidence.name
}

output "eval_bucket_name" {
  description = "GCS bucket for evaluation datasets + results."
  value       = google_storage_bucket.eval.name
}

output "cluster_config_bucket_name" {
  description = "GCS bucket holding clusters.json (the agent's cluster registry)."
  value       = google_storage_bucket.cluster_config.name
}

output "model_armor_template" {
  description = "Model Armor template name used by the agent."
  value       = google_model_armor_template.sre_agent.name
}

output "custom_mcp_url" {
  description = "URL of the custom Cloud Run MCP fallback (null when enable_custom_mcp = false)."
  value       = var.enable_custom_mcp ? google_cloud_run_v2_service.mcp[0].uri : null
}

output "gke_remote_mcp_url" {
  description = "Google-managed GKE Remote MCP endpoint (the agent's primary MCP source)."
  value       = local.gke_remote_mcp_url
}

output "wif_provider" {
  description = "Workload Identity Federation provider resource name — set as GCP_WIF_PROVIDER in GitHub Actions."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_sa_email" {
  description = "CI/CD deployer service account email — set as GCP_DEPLOYER_SA in GitHub Actions, and used by the iac/gke-access stack to grant its Project B roles."
  value       = google_service_account.deployer.email
}
