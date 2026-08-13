#!/usr/bin/env bash
# ============================================================================
# bootstrap_gke_access_deployer.sh — one-time grant of the CI deployer's
# Project B roles, so terraform-apply-gke-access.yml can manage
# iac/gke-access from CI instead of a human running `terraform apply` locally.
#
# WHY THIS EXISTS (same chicken-and-egg as bootstrap_wif.sh):
#   iac/gke-access/crossproject_iam.tf's deployer_b_roles (container.admin,
#   compute.networkAdmin, serviceusage.serviceUsageAdmin,
#   resourcemanager.projectIamAdmin, iam.roleAdmin) are only GRANTED once
#   Terraform applies them -- but CI authenticates as the deployer SA via WIF,
#   and a principal with zero roles in Project B cannot grant itself any
#   roles there. Confirmed live 2026-08-12:
#     gcloud projects get-iam-policy sreagent-demo --flatten="bindings[].members" \
#       --filter="bindings.members:sre-agent-deployer@..." --format="table(bindings.role)"
#   returned nothing -- the deployer SA had zero roles in Project B before this.
#
#   This script grants exactly those 5 roles once, out-of-band, using your own
#   (already-privileged) gcloud credentials. After this runs, set
#   deployer_sa_email in the CI workflow to the same SA and Terraform will
#   manage (and keep in sync) its own roles going forward -- same pattern as
#   Project A's bootstrap_wif.sh + create_wif=false.
#
# Roles must match local.deployer_b_roles in iac/gke-access/crossproject_iam.tf.
#
# USAGE:
#   PROJECT_A_ID=sreagent-t2-demo \
#   PROJECT_B_ID=sreagent-demo \
#   bash scripts/bootstrap_gke_access_deployer.sh
#
# Requires: gcloud, authenticated as a principal with IAM-admin rights on
#   Project B (e.g. Owner, or resourcemanager.projectIamAdmin +
#   iam.roleAdmin). Idempotent -- safe to re-run.
# ============================================================================
set -euo pipefail

: "${PROJECT_A_ID:?set PROJECT_A_ID (the agent project, where the deployer SA lives)}"
: "${PROJECT_B_ID:?set PROJECT_B_ID (the GKE target project)}"

SA_EMAIL="sre-agent-deployer@${PROJECT_A_ID}.iam.gserviceaccount.com"

DEPLOYER_B_ROLES=(
  roles/container.admin
  roles/compute.networkAdmin
  roles/serviceusage.serviceUsageAdmin
  roles/resourcemanager.projectIamAdmin
  roles/iam.roleAdmin
)

echo "==> Verifying deployer SA ${SA_EMAIL} exists in ${PROJECT_A_ID}"
gcloud iam service-accounts describe "${SA_EMAIL}" --project "${PROJECT_A_ID}" >/dev/null

echo "==> Granting ${#DEPLOYER_B_ROLES[@]} scoped roles in ${PROJECT_B_ID}"
for role in "${DEPLOYER_B_ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "${PROJECT_B_ID}" \
    --member "serviceAccount:${SA_EMAIL}" \
    --role "${role}" \
    --condition None \
    --quiet >/dev/null
  echo "    ${role}"
done

echo "==> Done. Next: set deployer_sa_email=${SA_EMAIL} in the gke-access CI workflow"
echo "    so Terraform now manages (and keeps in sync) these same roles going forward."
