# testing2-gcp-sre-agent — common tasks.
# Run `make help` for a summary.

.PHONY: help package-agent build-mcp tf-agent-init tf-agent-plan tf-agent-apply \
        tf-gke-init tf-gke-plan tf-gke-apply post-apply attach-gateway \
        register-endpoints smoke env fmt validate clean \
        kind-onprem-up onprem-onboard-kind onprem-check-kind onprem-cleanup-kind \
        kind-onprem-down ansible-lint

AGENT_DIR   := iac/agent
GKE_DIR     := iac/gke-access
ANSIBLE_DIR := ansible
REGION      ?= us-central1
MREGION     ?= us
KIND_CLUSTERS := sre-lab sre-lab-2

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

# ── Agent Registry ─────────────────────────────────────────────────────────
# Registration itself is Terraform-managed (iac/agent/agent_registry{,_mcp}.tf) as of
# 2026-09-04 — there is no longer a `register-endpoints` target, because `terraform
# apply` does it. Only the custom MCP's tool-spec artifact is generated outside
# Terraform, the same way agent.tar.gz is.
mcp-tool-spec:  ## Generate mcp/tool_spec.json (read by Terraform via file(); run before plan/apply)
	python3 scripts/build_mcp_tool_spec.py

attach-gateway:  ## Bind the reasoning engine to the Agent Gateway
	@bash scripts/attach_gateway_to_engine.sh

post-apply: attach-gateway  ## Run all post-apply steps

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

# ── On-prem cluster onboarding (Ansible) — disposable kind validation lab ───
# Kind lifecycle is deliberately separate from the reusable onboarding role/
# playbooks (openspec/changes/onprem-cluster-ansible-onboarding/): the role
# knows nothing about kind and runs unmodified against a real cluster.
kind-onprem-up:  ## Create sre-lab + sre-lab-2 kind clusters if they don't already exist
	@for c in $(KIND_CLUSTERS); do \
		if kind get clusters 2>/dev/null | grep -qx "$$c"; then \
			echo "kind cluster $$c already exists"; \
		else \
			kind create cluster --name $$c; \
		fi; \
	done

onprem-onboard-kind:  ## Run the reusable Ansible onboarding workflow against the kind test lab
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/onboard.yml

onprem-check-kind:  ## Re-verify already-onboarded kind clusters (read-only, no mutation)
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/verify.yml

onprem-cleanup-kind:  ## Ownership-safe cleanup of Fleet/RBAC objects this workflow created (does NOT delete kind clusters)
	cd $(ANSIBLE_DIR) && ansible-playbook playbooks/cleanup.yml

kind-onprem-down: onprem-cleanup-kind  ## Clean up Fleet/RBAC, then delete the sre-lab + sre-lab-2 kind clusters
	@for c in $(KIND_CLUSTERS); do kind delete cluster --name $$c; done

ansible-lint:  ## Lint the Ansible onboarding workflow
	cd $(ANSIBLE_DIR) && ansible-lint
