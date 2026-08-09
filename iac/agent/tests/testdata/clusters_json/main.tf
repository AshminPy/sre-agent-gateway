# Isolated test-only module — mirrors ONLY the clusters.json rendering logic
# from ../../main.tf and ../../variables.tf (the default_cluster/all_clusters/
# clusters_json locals + the additional_clusters variable + the collision
# check). Deliberately has NO providers and NO real resources, so the
# config/template test in ../clusters_json.tftest.hcl can run in plan mode
# with zero GCP credentials and zero side effects — that's the whole point of
# keeping this test layer separate from the real connectivity test.
#
# Maintenance note: if the clusters_json rendering logic in ../../main.tf
# changes, mirror the change here too — there is no single-source-of-truth
# link between this file and the real module (a Terraform test module can't
# `source` a subset of the parent module's locals). Flagged as a known
# duplication-drift risk, not hidden.

variable "gke_cluster_name" {
  type    = string
  default = "sre-test-cluster"
}

variable "project_b_id" {
  type    = string
  default = "test-project-b"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "additional_clusters" {
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

  # Real, enforcing guard — mirrors variables.tf. A `check` block was here
  # originally and was rejected in review: `check` blocks only emit a
  # warning, they never fail plan/apply. trimspace() on both sides so a
  # whitespace-padded key can't bypass the comparison.
  validation {
    condition = !contains(
      [for k in keys(var.additional_clusters) : trimspace(k)],
      trimspace(var.gke_cluster_name)
    )
    error_message = "additional_clusters contains a key that collides with var.gke_cluster_name (after trimming whitespace)."
  }
}

locals {
  default_cluster = {
    (var.gke_cluster_name) = {
      aliases            = []
      project            = var.project_b_id
      region             = var.region
      type               = "gke"
      environment        = "production"
      allowed_namespaces = []
      owner              = ""
      enabled            = true
    }
  }

  all_clusters = merge(local.default_cluster, var.additional_clusters)

  clusters_json = jsonencode({
    clusters = [
      for name, c in local.all_clusters : {
        name               = name
        aliases            = c.aliases
        project            = c.project
        region             = c.region
        type               = c.type
        environment        = c.environment
        allowed_namespaces = c.allowed_namespaces
        owner              = c.owner
        enabled            = c.enabled
      }
    ]
  })
}


output "clusters_json" {
  value = local.clusters_json
}

output "all_clusters" {
  value = local.all_clusters
}

output "cluster_count" {
  value = length(local.all_clusters)
}
