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
}

# Seed the eval dataset so `eval.py`/the eval suite can run out of the box.
resource "google_storage_bucket_object" "eval_dataset" {
  name   = "datasets/sre-agent-eval-v1.jsonl"
  bucket = google_storage_bucket.eval.name
  source = "${path.module}/../../eval/dataset.jsonl"
}
