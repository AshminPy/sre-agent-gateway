# Remote state backend (recommended for any shared/team deployment).
#
# Terraform state can contain sensitive values, so store it in a versioned,
# uniform-bucket-level-access GCS bucket in Project A. Create the bucket once
# as a prerequisite (see README), then either uncomment and edit the block
# below, or pass it at init time:
#
#   terraform init -backend-config="bucket=YOUR_PROJECT_A-tfstate" \
#                  -backend-config="prefix=agent"
#
# For a quick local trial you can skip the backend entirely (state stays in a
# local terraform.tfstate, which is gitignored).

terraform {
  backend "gcs" {
    # bucket = "YOUR_PROJECT_A-tfstate"   # set via -backend-config or uncomment
    prefix = "agent"
  }
}
