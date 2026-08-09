# RCA Report — Vertex AI Agent Gateway Binding Failure

**Prepared for:** Management review
**Status:** ✅ RESOLVED — verified end-to-end on both affected projects
**Date range:** 2026-07-15 to 2026-07-17
**Systems affected:** `sreagent-t2-demo` (production SRE agent deployment), plus a clean-room verification project (`sreagent-cleanroom-test`, now decommissioned) and a module-isolation test project (`agent-works-502620`, now decommissioned)

---

## 1. Executive Summary

Our AI-powered SRE investigation agent runs on Google Cloud's Vertex AI Agent Engine and routes all of its outbound network calls through an **Agent Gateway** — a Google-managed governance layer that authorizes and inspects traffic. Binding the agent to its gateway is a required step: without it, the agent's outbound calls to Gemini, Google APIs, and our Kubernetes clusters fail.

Our production deployment (`sreagent-t2-demo`) could not complete this binding step. Every attempt failed with a generic error and no further detail. This blocked the agent from being usable in that environment.

The investigation found **two separate, unrelated root causes**, fixed both, and proved the fix works with three independent live tests before declaring the issue resolved. The system is now fully operational.

**Root cause #1 (platform-wide, affects every deployment):** Google's Agent Gateway performs TLS inspection using a certificate it generates per-gateway. Our reasoning engine only trusts that certificate if the code deployment and the gateway binding are submitted to Google as a single combined request — not as two separate requests, even seconds apart. We were submitting them separately.

**Root cause #2 (specific to our production project):** even after fixing #1, our production engine still could not bind. After ruling out every configuration difference between our working and failing environments — and after building two separate isolated test environments specifically to prove it wasn't our code, our cloud project, or our gateway — we found the actual cause: **the specific engine resource itself had accumulated bad internal state from 8+ prior failed binding attempts**, made while we were still diagnosing root cause #1. A fresh engine resource, with byte-for-byte identical configuration, bound successfully on the first try, every time we tested it. The fix was to delete and recreate that one resource.

**Result:** the SRE agent now works end-to-end in production — investigates real Kubernetes incidents, reaches clusters in other projects, produces correct root-cause reports, and does so through the fully governed, security-inspected gateway path as designed.

---

## 2. Timeline

| Date | Phase |
|---|---|
| 2026-07-15 | Investigation begins. Original symptom: `sreagent-t2-demo`'s reasoning engine fails to bind to its Agent Gateway. |
| 2026-07-15 | Systematic elimination of standard causes: env vars, IAM, gateway config, endpoint registration — no single difference explains it. |
| 2026-07-16 | Built an isolated clean-room GCP project to separate "is this our code" from "is this our project." Root cause #1 (bundled-PATCH requirement) found and fixed there — verified end-to-end. |
| 2026-07-16 | Applied the same fix to `sreagent-t2-demo` directly. **Still failed identically.** Investigation continues — this was the first sign of a second, distinct problem. |
| 2026-07-16 – 2026-07-17 | Exhaustive, evidence-based elimination of every remaining hypothesis for `sreagent-t2-demo`'s specific failure: gateway network configuration, IAM roles and service agents, organization policy, container-level logs, documentation-referenced known issues. All ruled out with direct evidence, not assumption. |
| 2026-07-17 | Full 49-point audit of the implementation against Google's official documentation, covering Terraform, Python code, IAM, networking, and the investigation's own rigor. Found the one remaining structural gap: our own deployment code had never been tested fresh in an isolated project — only a reference implementation had. |
| 2026-07-17 | Two controlled experiments run to close that gap. Both succeeded, ruling out our code, our project, and our gateway as possible causes. |
| 2026-07-17 | Final test: recreated the specific engine resource in `sreagent-t2-demo`. **Bound successfully on the first attempt.** Full functional test passed. **Investigation closed — issue resolved.** |

---

## 3. Errors Observed (verbatim, in order encountered)

| # | Error | Where | What it meant |
|---|---|---|---|
| 1 | `SSLError: certificate verify failed: self-signed certificate in certificate chain` (calling `us-central1-aiplatform.mtls.googleapis.com`) | Original symptom, reproduced in the clean-room project | The engine didn't trust the gateway's TLS-inspection certificate — root cause #1. |
| 2 | `{"code": 3, "message": "The Reasoning Engine failed to be updated."}` | Every failed bind attempt on `sreagent-t2-demo`, 8+ times over two days | Generic Google API error, no further detail available even from Cloud Audit Logs — the central mystery of this investigation. |
| 3 | `{"code": 3, "message": "The Reasoning Engine failed to be updated.\n Please refer to our troubleshooting pages (...)"}` | Same failure, 2026-07-17 — first time this message included a documentation link | Led us to a real (but ultimately non-matching) VPC Service Controls troubleshooting scenario in Google's docs — see §6. |
| 4 | `Resource 'projects/.../authzPolicies/...' was not found` (HTTP 404) | Checking gateway-level IAM policy directly | Dead end — this API surface doesn't support that kind of query for this resource type. |
| 5 | `PERMISSION_DENIED... Access Context Manager API has not been used... SERVICE_DISABLED` | Attempting to check VPC Service Controls membership | This specific check remained permanently unavailable to us — the one thing in the entire investigation we could never directly verify (see §7). |
| 6 | `403 Forbidden. {'message': 'Egress request is not authorized.'}` | Runtime test of a temporary diagnostic engine (§8, Experiment 2) | Expected and explained — that temporary engine's own identity had never been granted a specific permission. Not related to the main investigation. |

---

## 4. Root Cause #1 — mTLS Certificate Trust (platform-wide)

**What we found:** Google's Agent Gateway performs TLS inspection on outbound traffic using a certificate authority it provisions specifically for that gateway. Our reasoning engine's trust store only receives that certificate authority when the code deployment request and the gateway-binding request are submitted to Google **together, as one atomic API call**. Our deployment process (Infrastructure-as-Code for the code, then a separate follow-up call for the gateway binding) never satisfied that — every deploy pushed code while the engine wasn't yet gateway-bound, so there was nothing for Google's provisioning pipeline to attach a trusted certificate to.

**How we found it:** built an isolated test project, reproduced the failure, then tested the fix directly by combining both requests into a single API call — matching the pattern used in Google's own official example code. Verified working immediately.

**The fix:** our deployment script now bundles the source code deployment and the gateway-binding configuration into one request, every time.

**Reference:** [Vertex AI Agent Gateway deployment guide](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/agent-gateway-runtime-deploy) (Google's own documented example uses this same combined-request pattern).

---

## 5. Root Cause #2 — Accumulated State on a Specific Engine Resource (`sreagent-t2-demo` only)

Fixing root cause #1 and applying it directly to `sreagent-t2-demo` did **not** resolve the failure there — it kept failing with the same generic error. This meant a second, unrelated problem existed specifically in that environment.

### 5.1 What we ruled out, and how (every item verified with direct evidence, not assumption)

| Candidate cause | How we checked | Result |
|---|---|---|
| Engine created before a documented compatibility cutoff date | Compared actual creation timestamps against Google's documented date | Both projects' engines were created after the cutoff — not the cause |
| Gateway network configuration mismatch | Fetched and compared full live gateway configuration between the working and failing projects | One real difference found (a private-networking feature); confirmed via Google's own documentation that this feature is optional and doesn't apply to our setup |
| Missing IAM permissions | Checked every relevant role and Google-managed service account on both projects | Everything required was present; the failing project's error was never a permission error to begin with |
| Organization policy restrictions | Compared all 193 organization-level policy settings between both projects | Byte-for-byte identical |
| Background network/certificate noise in logs | Found a suspicious certificate error in the failing engine's own startup logs | Same error also appeared 274 times in the *working* project's logs at the same point in startup — confirmed as harmless background noise, not the cause |
| Missing Agent Registry endpoint registration | Compared registered endpoints between both projects | Identical |
| Cross-project GKE access misconfiguration | Verified all four required grants present in both configurations | Identical |
| Google Cloud VPC Service Controls (a network security boundary) | Attempted to check directly — **the one thing we could not fully verify** | The Google Cloud feature needed to check this was disabled on our accounts and could not be enabled without a separate approval step; this remained an open question throughout, though a related documentation search found no matching error signature for our specific symptom |

### 5.2 The two experiments that isolated the actual cause

By 2026-07-17, every configuration difference had been checked and eliminated, but the underlying cause was still unconfirmed. A structured review of the entire investigation (see §9 — Full Audit) identified one gap: **our own deployment code had never actually been tested by deploying it fresh into a brand-new, untouched project.** The one successful proof we had (root cause #1's fix) used a *reference implementation* provided by Google, not our own code, for the gateway piece specifically.

Two controlled experiments closed this gap:

**Experiment 1 — is it our code?**
Deployed our own Terraform configuration (not Google's reference version) into a brand-new, temporary GCP project, changing nothing except the project itself. Result: **bound successfully on the first attempt.** Full functional test passed — the agent correctly investigated a test incident and produced a correct diagnosis.
→ **Conclusion: our code is not the cause.**

**Experiment 2 — is it the project, or the gateway?**
Created a second, temporary engine resource directly inside `sreagent-t2-demo` — the same project, using the exact same gateway that had failed to bind our real engine 8+ times. Result: **bound successfully on the first attempt.**
→ **Conclusion: neither the project nor the gateway is the cause.**

With code, project, and gateway all independently cleared, only one variable remained: the specific engine resource itself.

### 5.3 Final test and resolution

Deleted the original, always-failing engine resource and recreated it with identical configuration (same code, same settings, same identity type). **Result: bound successfully on the first attempt.** A full end-to-end functional test then confirmed the complete system working — investigation, cluster access, and root-cause reporting all functioning correctly through the gateway.

**Conclusion:** the original engine resource had accumulated some form of internal state — most likely tied to its history of 8+ failed binding attempts made earlier in this investigation, before root cause #1 was understood — that permanently blocked any further binding attempt, regardless of what configuration was applied to it. A fresh engine resource does not carry this state.

**The practical fix for any future recurrence:** if a reasoning engine has failed to bind multiple times and every configuration check comes back clean, recreate the engine resource rather than continuing to retry the same one.

---

## 6. A Documentation Lead That Didn't Pan Out (included for transparency)

Partway through, a bind attempt's error message included — for the first time — a link to a Google troubleshooting page. That page described a real, documented failure mode: a missing network security rule that can block a reasoning engine from starting up. This looked promising, since it touched the one area (VPC Service Controls) we could never fully verify.

On close inspection, it didn't match: the documented symptom text was different from what we were actually seeing, and it described a different stage of failure (the engine failing to *start up*, not our problem, which happened *before* startup, at the binding step itself). We recorded this clearly rather than treating it as confirmation of something it didn't actually prove — the eventual root cause (§5) is unrelated to this documentation lead.

---

## 7. What We Still Cannot Fully Rule Out (transparency note)

**VPC Service Controls perimeter membership** — a Google Cloud network security boundary feature — could not be directly checked, because the Google Cloud API needed to check it was disabled on our account and enabling it requires a separate approval step we did not take. Everything else in this investigation points to the resolution in §5.3 as the complete and correct fix (proven by three independent live tests), but this one item was never fully closed as a possibility on its own. It's flagged here for completeness, not because it changes the conclusion.

---

## 8. Separate Issues Found (not related to this investigation, tracked independently)

A broader implementation audit, run alongside the final stages of this investigation, found several unrelated code-quality issues. These do not affect the resolution above and are being tracked as separate work items so they don't get lost, but also don't hold up this report:

- Two network endpoint naming mismatches (Cloud Trace, Model Armor) — cosmetic today, since neither feature is currently active in production.
- Two real code correctness issues in the agent's own logic (a response-parsing edge case, and a content-safety check whose result wasn't being acted on).
- A handful of diagnostic/tooling improvements to our deployment script.

Filed as GitHub issues [#29](https://github.com/AshminPy/sre-agent-gateway/issues/29)–[#36](https://github.com/AshminPy/sre-agent-gateway/issues/36) for follow-up.

---

## 9. Full Audit (supporting document)

Before accepting §5's conclusion, a full, independent, evidence-based audit was run across every layer of the system — infrastructure code, application code, live cloud configuration, IAM, networking, and the investigation's own record — checked against Google's official documentation line by line. Every one of 49 individual checks was scored explicitly as confirmed-correct, confirmed-incorrect, or genuinely unknown (never guessed). Full detail in [`RESOLVED_2026-07-17_AUDIT_REPORT.md`](RESOLVED_2026-07-17_AUDIT_REPORT.md).

---

## 10. Verification Evidence

The fix was proven, not assumed, at every stage:

- ✅ Clean-room project: full agent → gateway → cross-project Kubernetes access → AI reasoning → report pipeline, working end-to-end (2026-07-16).
- ✅ Fresh-project code test (Experiment 1): same full pipeline, working end-to-end, using our own deployment code (2026-07-17).
- ✅ Production project, final state (2026-07-17): same full pipeline, working end-to-end, in `sreagent-t2-demo` itself — investigated a real test incident, correctly identified the root cause, reached a Kubernetes cluster in a separate project, produced a complete report.

## 11. Lessons Learned

1. **When a cloud platform requires two related changes to be submitted together, "submitted moments apart" is not the same as "submitted together."** This cost significant time before being identified — Google's own reference example showed the pattern, but our own documentation reading alone hadn't surfaced the requirement.
2. **A resource that has failed the same operation many times should be considered a suspect in its own right, not just its configuration.** Once we started treating "the engine itself" as a variable to test — not just "the engine's settings" — the remaining mystery resolved immediately.
3. **Prove code correctness by testing your own code, not a reference implementation, in isolation.** Our first "decisive" clean-room test used Google's reference gateway module, not ours — which left a real gap open for a full day of investigation before it was noticed and closed.
4. **Keep unrelated findings out of the main investigation.** Several real, independent issues turned up along the way; tracking them separately (§8) kept this report focused and made both efforts easier to act on.

---

## Full Investigation Record

For complete chronological detail, including every command run, every raw API response, and every hypothesis tested (including the ones not summarized above):
- [`RESOLVED_2026-07-17_TROUBLESHOOTING_LOG.md`](RESOLVED_2026-07-17_TROUBLESHOOTING_LOG.md) — full, append-only evidence log
- [`RESOLVED_2026-07-17_CURRENT_STATE.md`](RESOLVED_2026-07-17_CURRENT_STATE.md) — current system state summary
- [`RESOLVED_2026-07-17_FINAL_RCA.md`](RESOLVED_2026-07-17_FINAL_RCA.md) — technical root-cause document (engineering audience)
- [`RESOLVED_2026-07-17_AUDIT_REPORT.md`](RESOLVED_2026-07-17_AUDIT_REPORT.md) — the full 49-point implementation audit
- [`sreagent-gateway-verified`](https://github.com/AshminPy/sreagent-gateway-verified) — standalone repo proving our own deployment code works, built and verified during Experiment 1
