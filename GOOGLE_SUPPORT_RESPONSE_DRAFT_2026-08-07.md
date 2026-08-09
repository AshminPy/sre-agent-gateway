Hi [Customer Engineer name],

Thanks for the thorough write-up — really appreciate the depth, and the honesty about which parts are confirmed vs. best-effort. Following up on a few items, starting with #2 since we did some real testing on it.

**#2 — mTLS cert error.** We appreciate the suggestion, but we'd already ruled out `GOOGLE_API_USE_MTLS_ENDPOINT`/`GOOGLE_API_USE_CLIENT_CERTIFICATE` before filing this case — we did a direct source read of the installed `google-genai` 1.47.0 package and confirmed the Vertex endpoint URL is hardcoded, with no mTLS branch and no read of either env var anywhere in that library. `google-genai` never imports `google-api-core` (the library that does implement that switching logic, but only in a code path our client doesn't touch).

To be thorough, since your reply raised the question of whether our findings were stale, we re-ran the test today (2026-08-07) against our current live deployment: set `GOOGLE_API_USE_MTLS_ENDPOINT=never`, redeployed, and ran a real investigation end-to-end. No SSL error occurred — with or without the var. The investigation completed successfully both ways, confirming the env var makes no observable difference here.

Our actual root cause (documented in our `archive/RESOLVED_2026-07-17_FINAL_RCA.md`, verified via a decisive cross-project experiment): Agent Gateway provisions a self-signed root CA for its TLS inspection, and the reasoning engine's trust store only receives that CA when the source-code deploy and the `agentGatewayConfig` gateway binding are submitted in a single atomic `UpdateReasoningEngine` PATCH — never as two separate calls. We were splitting these (Terraform for source, a standalone script for the gateway attach), which is what caused the cert-verify failure. Bundling them into one atomic PATCH fixed it, reproduced successfully on two separate projects.

Given this, we'd actually push back gently on "the single-request workaround is a coincidence, not a design constraint" — in our case it wasn't a workaround, it was the fix, and we can point to the specific mechanism (CA provisioning tied to the atomic PATCH) rather than a correlation. Happy to share the full RCA if useful for your internal tracking — and still agree this deserves a documented doc bug, since neither the bundling requirement nor the CA-provisioning trigger is called out anywhere we could find.

One thing from your reply we haven't checked yet and would love more detail on if you find it: the `configure_mtls_channel()` issue where internal token-refresh/IAM calls bypass the mTLS channel. We didn't hit an mTLS-shaped failure in today's re-test, but if you're able to find the affected client library version list, we'll check our pins against it as a precaution.

**#4 — Model Armor + TLS.** Makes sense once we separated the two hops in our own head — the gateway terminating client TLS at its own edge (where our cert issue actually lived) vs. the gateway's own separate connection onward to Vertex. Not a conflict with what we found, just describing different segments. And confirmed the `streamQuery`/ADK caveat doesn't apply to us — we're LangGraph-based, don't use `streamQuery`.

**#6 — Memory Bank visibility.** Checked our code — we do write through Memory Bank's API already, not a self-managed store, so that shouldn't be our issue. If memories still aren't showing in the Console tab despite that, we may follow up separately with specifics (run IDs, timestamps) if it becomes a real blocker.

**#7 — Model Armor false positives.** This is genuinely useful, thank you — we hadn't seen the HIGH confidence-level recommendation for prompt injection/jailbreak detection called out that clearly. We're implementing: HIGH confidence on the template handling our K8s log/operational data, checking org floor settings first per your gotcha, and splitting templates by path (log-ingestion vs. chat). Will report back if false positives persist after that.

**#3 — GKE Remote MCP Preview status.** Understood on the Pre-GA terms — appreciate you flagging no committed GA date. We'll factor that into our own launch risk documentation. Let us know if the PM turns up anything more concrete.

**#5 — Connect Gateway.** Good to have this confirmed — matches what we've already built and verified end-to-end (real Fleet registration, short-lived credentials via `gke-gcloud-auth-plugin`, read-only RBAC proven).

Thanks again for the detailed pass on all of this — let us know if a call still makes sense given where things landed, particularly on the golden-dataset/RCA-training question (#1) which we didn't get real traction on yet.

[Your name]
