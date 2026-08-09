# Core data lookups and derived values shared across the stack.
#
# Projects are pre-created (a documented prerequisite) — we look them up rather
# than create them, so the deployer never needs org-level project/billing roles.

data "google_project" "a" {
  project_id = var.project_a_id
}

locals {
  # The reasoning engine's numeric ID (last path segment of its resource name).
  # Derived from the created resource so nothing is hardcoded.
  engine_numeric_id = element(reverse(split("/", google_vertex_ai_reasoning_engine.sre_agent.id)), 0)

  # The runtime Agent Identity principal (SPIFFE). identity_type = AGENT_IDENTITY
  # means the engine runs as this principal, not a service account. Every runtime
  # role is granted to THIS member (see iam.tf).
  #   principal://agents.global.org-{ORG}.system.id.goog/resources/aiplatform/projects/{PROJECT_NUMBER}/locations/{REGION}/reasoningEngines/{ENGINE_ID}
  agent_identity_member = "principal://agents.global.org-${data.google_project.a.org_id}.system.id.goog/resources/aiplatform/projects/${data.google_project.a.number}/locations/${var.region}/reasoningEngines/${local.engine_numeric_id}"

  # The org-wide principalSet covering ALL Agent Engine agents in Project A.
  # Used for the cross-project GKE grants in the iac/gke-access stack (it needs
  # only the project number, not a specific engine ID). Exported as an output.
  agent_identity_principal_set = "principalSet://agents.global.org-${data.google_project.a.org_id}.system.id.goog/attribute.platformContainer/aiplatform/projects/${data.google_project.a.number}"

  # Agent Registry path used by the gateway.
  registry_uri = "//agentregistry.googleapis.com/projects/${var.project_a_id}/locations/${var.region}"

  # Fixed Google-managed GKE Remote MCP endpoint (the agent's primary MCP source).
  gke_remote_mcp_url = "https://container.googleapis.com/mcp/read-only"

  # Cluster registry the agent reads at runtime (uploaded to the cluster-config
  # bucket). The default cluster (var.gke_cluster_name/project_b_id/region) is
  # always present, merged with any var.additional_clusters entries. Terraform
  # is the sole source of truth here — the bucket object below has no
  # lifecycle.ignore_changes, so this always fully overwrites clusters.json on
  # apply; hand-edits in GCS are not supported, add clusters via
  # var.additional_clusters instead.
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

# The default/additional-cluster name collision guard lives on
# var.additional_clusters itself (variables.tf) as a real `validation` block,
# not a `check` block here — `check` blocks only emit a warning and never
# fail plan/apply, which would have silently let an additional cluster
# override the default entry via merge() above.
