## 1. Build
- [x] 1.1 `package_agent.py` accepts `AGENT_SRC_DIR` / `AGENT_OUT_PATH` overrides; default output byte-identical
- [x] 1.2 `make package-candidate` (requires `CANDIDATE_AGENT_SRC`); `agent-candidate.tar.gz` git-ignored

## 2. Terraform
- [x] 2.1 `enable_candidate_agent` variable (default false)
- [x] 2.2 Candidate engine + candidate memory bank (count-gated)
- [x] 2.3 Candidate identity grants mirroring the primary list
- [x] 2.4 Outputs for candidate engine ID and identity
- [x] 2.5 `terraform validate` passes; plan with flag off (CI-equivalent vars, candidate archive absent) shows no resource changes. `terraform test` cannot run on the pinned 1.4.7 (known repo gap)

## 3. Review
- [x] 3.1 Independent review (terraform-reviewer): 1 MUST FIX (filebase64 evaluated at count=0) fixed and re-verified; CI-destroys-candidate behaviour documented

## 4. Deploy and validate
- [x] 4.1 Plan with flag on: only additions
- [x] 4.2 Apply (18 added, 0 changed, 0 destroyed); candidate engine 4863794688827588608 live; primary updateTime unchanged
- [x] 4.3 Add candidate principal to `k8s/rbac.yaml`; apply; verify binding
- [x] 4.4 Smoke investigation on candidate engine succeeds (crashloop-001, oomkilled-001: confirmed; imagepull-001: insufficient_evidence -- see 4.5)
- [x] 4.5 Same eval cases run against both engines; results compared (2026-09-25: 14 sanitized why-scenarios, 12 valid; personal 6/11 L3, candidate 4/11 L3 + 4 withheld by its causal verifier; report kept outside the repo)
