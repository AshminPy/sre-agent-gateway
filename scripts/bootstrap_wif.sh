#!/usr/bin/env bash
# ============================================================================
# bootstrap_wif.sh — one-time creation of the CI/CD deployer identity.
#
# WHY THIS EXISTS (the chicken-and-egg):
#   The git-driven flow runs `terraform apply` inside GitHub Actions, which
#   authenticates AS the deployer service account via Workload Identity
#   Federation. A deployer cannot create the very identity it runs as, so that
#   identity must be seeded out-of-band — once — before the first CI apply.
#   After this runs, deploy `iac/agent` with `-var="create_wif=false"` (the CI
#   workflows already pass this) so Terraform does NOT try to re-create these.
#
#   If instead you want a self-contained LOCAL apply that bootstraps everything,
#   skip this script and leave create_wif=true (the default). You only need this
#   for the GitHub Actions path.
#
# WHAT IT CREATES (identical to iac/agent/oidc.tf — that file is the source of
#   truth for the role list; keep the two in sync):
#     • service account  sre-agent-deployer@<PROJECT_A>.iam.gserviceaccount.com
#     • the scoped deployer roles in Project A
#     • objectAdmin on the Terraform state bucket
#     • WIF pool  github-pool  + provider  github-provider  (scoped to your repo)
#     • workloadIdentityUser binding letting the repo impersonate the SA
#
# USAGE:
#   PROJECT_A_ID=my-agent-proj \
#   GITHUB_REPO=my-org/testing2-gcp-sre-agent \
#   TFSTATE_BUCKET=my-agent-proj-tfstate \
#   bash scripts/bootstrap_wif.sh
#
# Requires: gcloud, authenticated as a principal with Owner or the equivalent
#   IAM-admin rights on Project A. Idempotent — safe to re-run.
# ============================================================================
set -euo pipefail

: "${PROJECT_A_ID:?set PROJECT_A_ID (the agent project)}"
: "${GITHUB_REPO:?set GITHUB_REPO as owner/name, e.g. my-org/testing2-gcp-sre-agent}"
TFSTATE_BUCKET="${TFSTATE_BUCKET:-}"

SA_ID="sre-agent-deployer"
SA_EMAIL="${SA_ID}@${PROJECT_A_ID}.iam.gserviceaccount.com"
POOL_ID="github-pool"
PROVIDER_ID="github-provider"

# Roles must match local.deployer_a_roles in iac/agent/oidc.tf.
DEPLOYER_ROLES=(
  roles/serviceusage.serviceUsageAdmin
  roles/iam.serviceAccountAdmin
  roles/iam.serviceAccountUser
  roles/iam.workloadIdentityPoolAdmin
  roles/resourcemanager.projectIamAdmin
  roles/compute.networkAdmin
  roles/compute.securityAdmin
  roles/aiplatform.admin
  roles/networkservices.editor
  roles/networksecurity.editor
  roles/modelarmor.admin
  roles/run.admin
  roles/artifactregistry.admin
  roles/monitoring.editor
  roles/logging.configWriter
  roles/storage.admin
  roles/iap.admin
)

echo "==> Enabling required APIs for the WIF bootstrap"
gcloud services enable iam.googleapis.com iamcredentials.googleapis.com \
  sts.googleapis.com cloudresourcemanager.googleapis.com \
  --project "${PROJECT_A_ID}"

echo "==> Creating deployer service account ${SA_EMAIL}"
if ! gcloud iam service-accounts describe "${SA_EMAIL}" --project "${PROJECT_A_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SA_ID}" \
    --project "${PROJECT_A_ID}" \
    --display-name "SRE Agent CI/CD deployer (WIF)"
else
  echo "    already exists — skipping"
fi

echo "==> Granting ${#DEPLOYER_ROLES[@]} scoped roles in ${PROJECT_A_ID}"
for role in "${DEPLOYER_ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "${PROJECT_A_ID}" \
    --member "serviceAccount:${SA_EMAIL}" \
    --role "${role}" \
    --condition None \
    --quiet >/dev/null
  echo "    ${role}"
done

if [[ -n "${TFSTATE_BUCKET}" ]]; then
  echo "==> Granting objectAdmin on gs://${TFSTATE_BUCKET}"
  gcloud storage buckets add-iam-policy-binding "gs://${TFSTATE_BUCKET}" \
    --member "serviceAccount:${SA_EMAIL}" \
    --role roles/storage.objectAdmin >/dev/null
fi

echo "==> Creating Workload Identity Federation pool ${POOL_ID}"
if ! gcloud iam workload-identity-pools describe "${POOL_ID}" \
  --project "${PROJECT_A_ID}" --location global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "${POOL_ID}" \
    --project "${PROJECT_A_ID}" --location global \
    --display-name "GitHub Actions"
else
  echo "    already exists — skipping"
fi

echo "==> Creating OIDC provider ${PROVIDER_ID} (scoped to ${GITHUB_REPO})"
if ! gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
  --project "${PROJECT_A_ID}" --location global --workload-identity-pool "${POOL_ID}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
    --project "${PROJECT_A_ID}" --location global \
    --workload-identity-pool "${POOL_ID}" \
    --display-name "GitHub OIDC" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition "assertion.repository == '${GITHUB_REPO}'"
else
  echo "    already exists — skipping"
fi

PROJECT_A_NUMBER="$(gcloud projects describe "${PROJECT_A_ID}" --format='value(projectNumber)')"
POOL_NAME="projects/${PROJECT_A_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}"
PROVIDER_NAME="${POOL_NAME}/providers/${PROVIDER_ID}"

echo "==> Letting ${GITHUB_REPO} impersonate ${SA_EMAIL}"
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --project "${PROJECT_A_ID}" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/${POOL_NAME}/attribute.repository/${GITHUB_REPO}" \
  --quiet >/dev/null

cat <<EOF

============================================================================
WIF bootstrap complete. Set these GitHub Actions repo secrets:

  GCP_WIF_PROVIDER   = ${PROVIDER_NAME}
  GCP_DEPLOYER_SA    = ${SA_EMAIL}
  GCP_PROJECT_A_ID   = ${PROJECT_A_ID}
  TFSTATE_BUCKET     = ${TFSTATE_BUCKET}

Plus (not produced here): GCP_PROJECT_B_ID, GCP_REGION, NOTIFICATION_EMAIL.

Then deploy iac/agent with create_wif=false (the CI workflows already do this).
============================================================================
EOF
