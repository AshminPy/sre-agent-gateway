# Remote state for the observability/dashboard stack. Same bucket as the other
# stacks, different prefix — configure via -backend-config at init time:
#
#   terraform init -backend-config="bucket=YOUR_TFSTATE_BUCKET" \
#                  -backend-config="prefix=observability"

terraform {
  backend "gcs" {
    prefix = "observability"
  }
}
