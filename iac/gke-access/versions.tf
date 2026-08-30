# Provider and Terraform version pins for the GKE + cross-project-access stack.
#
# 2026-08-25: lowered from >= 1.6.0 to match company policy (pinned at 1.4.7).
# Checked this stack's own .tf files for anything that actually needs 1.5+
# (check blocks, import blocks, optional() with defaults, precondition/
# postcondition) -- none present. This stack has no CI (applied manually),
# and >= 1.6.0 had been unchanged since the repo's initial commit with no
# documented reason -- unlike ../agent/versions.tf's >= 1.9.0, which is a
# real, verified requirement (a cross-variable validation block). Validated
# live with the actual 1.4.7 binary, not just assumed compatible.

terraform {
  required_version = ">= 1.4.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 7.0.0, < 8.0.0"
    }
  }
}
