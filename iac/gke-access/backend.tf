# Remote state for the gke-access stack. Store in a versioned GCS bucket (may be
# the same bucket as the agent stack, with a different prefix). Configure via
# -backend-config or uncomment and edit.
#
#   terraform init -backend-config="bucket=YOUR_TFSTATE_BUCKET" \
#                  -backend-config="prefix=gke-access"

terraform {
  backend "gcs" {
    prefix = "gke-access"
  }
}
