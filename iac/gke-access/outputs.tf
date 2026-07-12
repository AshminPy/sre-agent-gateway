output "gke_cluster_name" {
  description = "Name of the GKE cluster the agent investigates."
  value       = var.gke_cluster_name
}

output "gke_cluster_location" {
  description = "Location of the GKE cluster (region)."
  value       = var.region
}

output "gke_cluster_endpoint" {
  description = "Endpoint of the created demo cluster (null when using an existing cluster)."
  value       = var.create_gke_cluster ? google_container_cluster.sre_test[0].endpoint : null
  sensitive   = true
}

output "agent_principal_set_granted" {
  description = "The agent principalSet that received cross-project read access in Project B."
  value       = local.agent_principal_set
}

output "crossproject_roles_granted" {
  description = "The read-only roles granted to the agent in Project B."
  value       = local.agent_crossproject_roles
}
