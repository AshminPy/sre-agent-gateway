# GCS buckets the agent uses at runtime, with BUCKET-LEVEL IAM (least privilege
# — the runtime identity gets object roles on exactly these buckets, never a
# project-wide storage role). Grants are defined in iam.tf.

# Evidence store — sanitized MCP responses + full RCA output per run.
resource "google_storage_bucket" "evidence" {
  project                     = var.project_a_id
  name                        = "${var.project_a_id}-evidence"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning { enabled = true }

  lifecycle_rule {
    condition { age = 90 }
    action { type = "Delete" }
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.apis]
}

# Evaluation datasets + results.
resource "google_storage_bucket" "eval" {
  project                     = var.project_a_id
  name                        = "${var.project_a_id}-eval"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning { enabled = true }

  lifecycle_rule {
    condition { age = 365 }
    action { type = "Delete" }
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.apis]
}

# Cluster registry — the agent reads clusters.json at startup to resolve which
# GKE cluster (in Project B) each incident targets.
resource "google_storage_bucket" "cluster_config" {
  project                     = var.project_a_id
  name                        = "${var.project_a_id}-cluster-config"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true

  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket_object" "clusters_json" {
  name    = "clusters.json"
  bucket  = google_storage_bucket.cluster_config.name
  content = local.clusters_json

  # Cluster-name collision guard. An additional_clusters key must not collide
  # with var.gke_cluster_name, or the two entries silently collapse into one
  # when agent/mcp_client.py's registry parser calls .strip() on every name.
  #
  # 2026-08-26: this lives here as a precondition (Terraform >= 1.2) rather
  # than as a cross-variable `validation` block on var.additional_clusters
  # (Terraform >= 1.9), so this stack stays deployable on the same 1.4.7 that
  # company Spacelift pins. A precondition fails plan and apply just like a
  # validation block; a `check` block would only warn, and was rejected in
  # review for exactly that reason. Condition text is unchanged from the
  # original, trimspace() on both sides included.
  lifecycle {
    precondition {
      condition = !contains(
        [for k in keys(var.additional_clusters) : trimspace(k)],
        trimspace(var.gke_cluster_name)
      )
      error_message = "additional_clusters contains a key that collides with var.gke_cluster_name (after trimming whitespace) — pick a distinct name for the additional cluster."
    }
  }
}

# Seed the eval dataset so `eval.py`/the eval suite can run out of the box.
resource "google_storage_bucket_object" "eval_dataset" {
  name   = "datasets/sre-agent-eval-v1.jsonl"
  bucket = google_storage_bucket.eval.name
  source = "${path.module}/../../eval/dataset.jsonl"
}

# issue #103 Slice 1: transient scratch I/O for Agent Engine long-running query jobs
# (run_query_job/check_query_job). Deliberately NOT the `evidence` bucket -- that bucket
# is durable/versioned/prevent_destroy=true for the real RCA record; mixing disposable
# async-transport input/output files into it would blur that distinction. The real RCA
# still lands in `evidence` unchanged, via the same _save_to_gcs() call inside query()
# that the synchronous path already uses. 7-day lifecycle is a PROPOSED operational
# value, not an established requirement -- revisit if real usage needs longer.
resource "google_storage_bucket" "query_jobs" {
  project                     = var.project_a_id
  name                        = "${var.project_a_id}-query-jobs"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true # disposable scratch data, unlike `evidence`

  lifecycle_rule {
    condition { age = 7 }
    action { type = "Delete" }
  }

  depends_on = [google_project_service.apis]
}
