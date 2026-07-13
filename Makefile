# testing2-gcp-sre-agent — common tasks.
# Run `make help` for a summary.

.PHONY: help package-agent build-mcp tf-agent-init tf-agent-plan tf-agent-apply \
        tf-gke-init tf-gke-plan tf-gke-apply post-apply attach-gateway \
        register-endpoints smoke env fmt validate clean

AGENT_DIR := iac/agent
GKE_DIR   := iac/gke-access
REGION    ?= us-central1
MREGION   ?= us

help:  ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

package-agent:  ## Build agent.tar.gz from agent/ (reproducible)
	@bash scripts/package_agent.sh

build-mcp:  ## Build & push the custom Cloud Run MCP image (only if enable_custom_mcp=true)
	@echo "Build the MCP image and push to Artifact Registry:"
	@echo "  gcloud builds submit mcp/ --tag $(REGION)-docker.pkg.dev/PROJECT_A/sre-agent-repo/sre-k8s-mcp:latest"

# ── GKE stack (Project B) — run first ──────────────────────────────────────
tf-gke-init:  ## terraform init for the gke-access stack
	terraform -chdir=$(GKE_DIR) init

tf-gke-plan:  ## terraform plan for the gke-access stack
	terraform -chdir=$(GKE_DIR) plan

tf-gke-apply:  ## terraform apply for the gke-access stack
	terraform -chdir=$(GKE_DIR) apply

# ── Agent stack (Project A) ────────────────────────────────────────────────
tf-agent-init:  ## terraform init for the agent stack
	terraform -chdir=$(AGENT_DIR) init

tf-agent-plan: package-agent  ## Package agent, then terraform plan the agent stack
	terraform -chdir=$(AGENT_DIR) plan

tf-agent-apply: package-agent  ## Package agent, then terraform apply the agent stack
	terraform -chdir=$(AGENT_DIR) apply

# ── Post-apply (gateway path) ──────────────────────────────────────────────
register-endpoints:  ## Register Google-API + MCP endpoints in Agent Registry (both regional + multi-region, per the official codelab)
	python3 scripts/register_endpoints.py \
		--project $$(terraform -chdir=$(AGENT_DIR) output -raw project_a_id) \
		--region $(REGION) --multi-region $(MREGION) --mtls-endpoints=include

attach-gateway:  ## Bind the reasoning engine to the Agent Gateway
	@bash scripts/attach_gateway_to_engine.sh

post-apply: register-endpoints attach-gateway  ## Run all post-apply steps

# ── Dev / verify ───────────────────────────────────────────────────────────
env:  ## Generate agent/.env from Terraform outputs
	@bash scripts/init-env.sh

smoke:  ## Invoke the agent and assert a well-formed RCA
	@bash scripts/smoke_test.sh

fmt:  ## terraform fmt both stacks
	terraform -chdir=$(AGENT_DIR) fmt -recursive
	terraform -chdir=$(GKE_DIR) fmt -recursive

validate: package-agent  ## terraform validate both stacks
	cd $(AGENT_DIR) && terraform init -backend=false -input=false >/dev/null && terraform validate
	cd $(GKE_DIR)   && terraform init -backend=false -input=false >/dev/null && terraform validate

clean:  ## Remove build artifacts
	rm -f agent.tar.gz
