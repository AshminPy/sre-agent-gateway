# Config/template test layer — proves the clusters.json rendering logic is
# correct, WITHOUT touching any real cloud resource or requiring GCP
# credentials. Runs against the isolated testdata/clusters_json module (see
# that module's own comment for why it's a separate mirror, not the real
# iac/agent module).
#
# This is deliberately scoped to config/rendering only:
#   - routing behavior (does resolve_cluster_routing() pick the right cluster
#     from a rendered registry) is covered by the Python test in
#     tests/test_multi_cluster_registry.py at the repo root — different layer.
#   - real GKE connectivity is covered by a live invoke_agent.py run against
#     the actual deployed clusters.json — different layer again.
#
# Run with: terraform test (from iac/agent/)

variables {
  gke_cluster_name = "sre-test-cluster"
  project_b_id     = "sreagent-demo"
  region           = "us-central1"
}

# ── Backward compatibility: default-only (no additional_clusters) must match
# today's exact single-cluster output — proves this refactor doesn't change
# behavior for every existing deployment that hasn't set the new variable. ──
run "default_only_matches_legacy_single_cluster_output" {
  command = plan

  module {
    source = "./tests/testdata/clusters_json"
  }

  assert {
    condition     = output.cluster_count == 1
    error_message = "Default (no additional_clusters) must render exactly one cluster, matching today's behavior."
  }

  assert {
    condition     = jsondecode(output.clusters_json).clusters[0].name == "sre-test-cluster"
    error_message = "Default cluster name must come from var.gke_cluster_name."
  }

  assert {
    condition     = jsondecode(output.clusters_json).clusters[0].project == "sreagent-demo"
    error_message = "Default cluster project must come from var.project_b_id."
  }

  assert {
    condition     = jsondecode(output.clusters_json).clusters[0].type == "gke"
    error_message = "Default cluster type must be gke, matching the legacy template's hardcoded value."
  }

  assert {
    condition     = jsondecode(output.clusters_json).clusters[0].enabled == true
    error_message = "Default cluster must be enabled, matching the legacy template."
  }
}

# ── Multi-cluster: 2 synthetic additional clusters render correctly alongside
# the default one — proves the core fix (multi-cluster support) works. ──
run "two_additional_synthetic_clusters_render_correctly" {
  command = plan

  module {
    source = "./tests/testdata/clusters_json"
  }

  variables {
    additional_clusters = {
      "synthetic-cluster-2" = {
        project     = "sreagent-demo"
        region      = "us-east1"
        type        = "gke"
        environment = "staging"
        owner       = "test-suite"
      }
      "synthetic-cluster-3" = {
        project            = "sreagent-demo-2"
        region             = "europe-west1"
        type               = "custom"
        environment        = "production"
        allowed_namespaces = ["team-a", "team-b"]
        owner              = "test-suite"
        enabled            = false
      }
    }
  }

  assert {
    condition     = output.cluster_count == 3
    error_message = "Default cluster + 2 additional clusters must total 3 entries."
  }

  assert {
    condition     = contains([for c in jsondecode(output.clusters_json).clusters : c.name], "sre-test-cluster")
    error_message = "The default cluster must still be present alongside the additional ones."
  }

  assert {
    condition     = contains([for c in jsondecode(output.clusters_json).clusters : c.name], "synthetic-cluster-2")
    error_message = "synthetic-cluster-2 must be present in the rendered output."
  }

  assert {
    condition     = contains([for c in jsondecode(output.clusters_json).clusters : c.name], "synthetic-cluster-3")
    error_message = "synthetic-cluster-3 must be present in the rendered output."
  }

  assert {
    condition     = [for c in jsondecode(output.clusters_json).clusters : c.enabled if c.name == "synthetic-cluster-3"][0] == false
    error_message = "synthetic-cluster-3's enabled=false override must be preserved, not silently defaulted to true."
  }

  assert {
    condition     = [for c in jsondecode(output.clusters_json).clusters : c.type if c.name == "synthetic-cluster-3"][0] == "custom"
    error_message = "synthetic-cluster-3's type=custom override must be preserved."
  }

  assert {
    condition     = [for c in jsondecode(output.clusters_json).clusters : c.allowed_namespaces if c.name == "synthetic-cluster-3"][0] == ["team-a", "team-b"]
    error_message = "synthetic-cluster-3's allowed_namespaces must be preserved exactly."
  }
}

# ── Collision guard: an additional cluster using the SAME name as the default
# cluster must fail the plan, not silently merge/override. This targets the
# real `validation` block on var.additional_clusters — a `check` block was
# tried first and rejected in review because `check` blocks only ever emit a
# warning, never a real plan/apply failure. ──
run "colliding_cluster_name_fails_the_check" {
  command = plan

  module {
    source = "./tests/testdata/clusters_json"
  }

  variables {
    additional_clusters = {
      "sre-test-cluster" = {
        project = "some-other-project"
        region  = "us-west1"
      }
    }
  }

  expect_failures = [
    var.additional_clusters,
  ]
}

# ── Same collision, but with a whitespace-padded key. Must still fail — a
# naive `contains(keys(...), ...)` comparison (without trimspace()) would
# miss this, and the resulting two "distinct" entries would silently
# collapse into one when agent/mcp_client.py's registry parser .strip()s
# every name, dropping the additional cluster from the runtime registry with
# no error anywhere. ──
run "whitespace_padded_colliding_name_still_fails" {
  command = plan

  module {
    source = "./tests/testdata/clusters_json"
  }

  variables {
    additional_clusters = {
      " sre-test-cluster" = {
        project = "some-other-project"
        region  = "us-west1"
      }
    }
  }

  expect_failures = [
    var.additional_clusters,
  ]
}
