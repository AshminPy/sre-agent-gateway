# Derive the agent's org-wide principalSet from Project A (no manual copying of
# long principal strings). This covers every Agent Engine agent in Project A and
# needs only Project A's number + org ID — so this stack has no dependency on the
# agent actually being deployed yet.

data "google_project" "a" {
  project_id = var.project_a_id
}

locals {
  agent_principal_set = "principalSet://agents.global.org-${data.google_project.a.org_id}.system.id.goog/attribute.platformContainer/aiplatform/projects/${data.google_project.a.number}"
}
