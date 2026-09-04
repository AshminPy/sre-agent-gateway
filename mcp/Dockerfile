FROM python:3.11-slim

WORKDIR /app

# gke-gcloud-auth-plugin (needed for the Connect Gateway / non-GKE cluster
# path — see get_k8s_clients()'s kube_context branch in server.py). It is a
# Cloud SDK component, not a standalone binary — official-package install per
# https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl#install_plugin,
# not the third-party standalone alternatives some guides suggest, since this
# plugin sits on a security-relevant credential path.
#
# google-cloud-cli (the base package, not just the auth-plugin package) is
# required too -- live-verified 2026-09-04: the auth plugin's own credential
# resolution shells out to `gcloud config config-helper --format=json`
# internally (confirmed via the deployed container's own error: `exec:
# "gcloud": executable file not found in $PATH`). Installing only
# google-cloud-cli-gke-gcloud-auth-plugin is not sufficient on its own.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl gnupg apt-transport-https ca-certificates \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
      | tee /etc/apt/sources.list.d/google-cloud-sdk.list \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
      | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-cloud-cli google-cloud-cli-gke-gcloud-auth-plugin \
    && apt-get purge -y --auto-remove gnupg apt-transport-https curl \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/usr/lib/google-cloud-sdk/bin:${PATH}"
ENV USE_GKE_GCLOUD_AUTH_PLUGIN=True

# Install uv for fast dependency install
RUN pip install uv

COPY requirements.txt .
RUN uv pip install --system -r requirements.txt

COPY . .

# Non-secret kubeconfig for the Connect Gateway / non-GKE path (see the file's
# own header comment). Only used when K8S_MCP_KUBE_CONTEXT selects this
# context at runtime — the direct-GKE-endpoint branch (GKE_CLUSTER_ENDPOINT +
# GKE_CA_CERT_GCS_PATH) ignores this file entirely.
ENV KUBECONFIG=/app/connect-gateway-kubeconfig.yaml

ENV PORT=8080
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python", "server.py"]
