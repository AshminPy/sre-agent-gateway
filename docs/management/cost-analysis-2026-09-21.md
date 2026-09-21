# SRE Agent Gateway — Cost Analysis (2026-09-21)

> **Correction, added after independent re-verification (same session, immediately after the
> report below was drafted):** §2's GKE cluster management-fee row and §3 item 2 both flagged
> a $0–$73/month "unresolved" swing over whether a *regional* Autopilot cluster qualifies for
> GKE's one-cluster-per-billing-account free credit. A second, independent WebSearch pass
> (querying Google's own docs domain specifically) resolved this: **all GKE Autopilot clusters
> are regional by design — there is no such thing as a zonal Autopilot cluster** — and Google's
> own free-tier description explicitly states the $74.40/month credit "covers management fees
> for both Autopilot and zonal Standard clusters equally." A regional-vs-zonal disqualification
> would be self-contradictory (it would mean no Autopilot cluster could ever qualify). **Net
> effect: the GKE cluster management fee is $0/month** (one cluster, fully covered by the free
> credit), not the $0–$73 range originally stated. This removes the only other meaningful swing
> factor besides the Agent Engine line — see the revised total below. The Agent Engine rate
> itself ($0.0864/vCPU-hr, $0.0090/GB-hr) was also independently re-checked via a second,
> differently-worded WebSearch pass and converged on the identical figures from multiple
> sources — raising confidence on that line from Medium to **Medium-High** (still not a direct
> official-page render, due to a tool limitation — see §6 — but now triangulated twice, not
> single-sourced).
>
> **Revised estimated total: ~$608–$615/month** (was $608–$681). The GKE fee uncertainty is
> resolved; the Agent Engine line (~$604/month, ~99% of the revised total) remains the only
> figure worth a final manual check against the live Billing Console before acting on this.

---

> ## SECOND CORRECTION — real invoiced data (user-supplied Billing Console screenshot,
> ## 2026-09-21) CONTRADICTS the modeled estimate above. This supersedes both corrections
> ## above for the primary cost driver. Modeled estimates are never as reliable as actual
> ## observed spend — this is exactly why §6 flagged the no-billing-export gap as the top
> ## follow-up. The user closed that gap manually via the mobile Billing Console.
>
> **Real data (Billing Console, "Sep 1 2025 – Sep 30 2026" window, which in practice means
> "since these projects existed" through today plus a forecast for the rest of September):**
>
> | Project | Actual cost | Forecasted additional |
> |---|---|---|
> | SRE Agent T2 Demo (`sreagent-t2-demo`) | $152.68 | $65.19 |
> | `sreagent-demo` | $38.42 | $2.24 |
> | `ai-adk-sre-agent-demo` | $0.01 | $0.00 |
>
> | Service | Actual cost | Forecasted additional |
> |---|---|---|
> | **Fleets** | **$114.60** | **$65.64** |
> | Vertex AI | $33.67 | $0.00 |
> | Networking | $16.72 | $2.31 |
> | Kubernetes Engine | $10.98 | $0.00 |
> | Cloud Monitoring | $7.27 | $0.00 |
>
> **The Vertex AI Agent Engine estimate in §1/§2 above (~$604/month) is WRONG — retracted.**
> Real total Vertex AI spend since the reasoning engine was created (2026-06-25, ~88 days ago)
> is $33.67 — roughly **$11–12/month**, not $604/month. The `min_instances=2` /
> `$0.0864/vCPU-hr` modeled math was internally consistent, but the underlying rate (sourced
> from WebSearch triangulation, never a direct official-page read — see the original §6 caveat)
> was evidently wrong, or Agent Engine's actual current billing behavior for this config differs
> from the assumption. **Lesson: a WebSearch-triangulated rate, even converging across multiple
> independent queries, is not a substitute for real invoiced data — it was wrong here despite
> "triangulation" raising confidence from Medium to Medium-High in the first correction above.
> That confidence upgrade was itself a mistake; real spend is the only real proof.**
>
> **The REAL #1 cost driver is "Fleets" — root-caused, not modeled:**
> `gcloud container fleet memberships list --project=sreagent-t2-demo` shows both on-prem
> `kind`-cluster fleet memberships registered at **`clusterTier: "ENTERPRISE"`**, not the free
> `STANDARD` tier:
> - `sre-lab` — 32 vCPU, 16.3 GiB, registered 2026-09-05
> - `sre-lab-2` — 16 vCPU, 8.1 GiB, registered 2026-09-09
>
> GKE Enterprise tier bills a genuine per-vCPU-hour fee for every registered fleet member
> cluster (confirmed via official docs: "any third-party clusters you register will incur a
> per-vCPU charge as part of your GKE pricing" — cloud.google.com/kubernetes-engine/pricing).
> The real numbers cross-validate cleanly: $114.60 actual over ~16 days (since `sre-lab`'s
> registration) ≈ $7.16/day; $65.64 forecasted over the ~9 remaining days of September ≈
> $7.29/day — consistent, confirming a real, steady, ongoing charge, not a one-time fee.
> **Extrapolated run rate: ~$216/month**, and rising slightly further once `sre-lab-2` (4 days
> younger) is fully reflected in a full month.
>
> **The fix is real and verified, not proposed on modeled data:** official GKE docs confirm
> (1) Connect Gateway — the actual mechanism this repo uses to reach these on-prem clusters —
> does **not** require Enterprise tier, and (2) fleet member clusters "can now be downgraded to
> the standard tier without removing them from their fleet" or losing Connect Gateway access.
> **Downgrading both `sre-lab` and `sre-lab-2` from `ENTERPRISE` to `STANDARD` tier should stop
> essentially all of the real $114.60–$216/month Fleets charge, with no loss of the
> functionality this repo actually uses.** The exact `gcloud`/API downgrade command was not
> yet located in this pass (`gcloud container fleet memberships update --help` did not show an
> obvious `--tier` flag) — RUNTIME VALIDATION REQUIRED before executing, and this is a live
> GCP config change requiring explicit user authorization before it's run, same as any other
> destructive/state-changing action in this repo's process.
>
> **Corrected revised total, using real data as ground truth:** ~$205–$220/month actual run
> rate today (dominated by Fleets ~$114–216/mo, not Vertex AI), with the Fleets tier-downgrade
> being the single highest-value, verified, low-risk cost fix — worth roughly 10x more monthly
> savings than anything identified in the original modeled analysis, and it doesn't touch the
> agent/cluster/RBAC work already scoped in `openspec/changes/environment-lifecycle-iac/`.


**Scope:** Read-only cost investigation. No resources changed. Covers both live GCP projects
on billing account `billingAccounts/0138AB-1B1BE5-05AFF5` (currency CAD):
Project A `sreagent-t2-demo` (327234009108) and Project B `sreagent-demo`.

**Method:** No BigQuery billing export exists on this billing account (confirmed:
`bq ls --project_id=sreagent-t2-demo` shows only the `sre_agent_investigations`
application dataset — no billing export dataset). All dollar figures below are therefore
either VERIFIED live resource sizes/configs, or ESTIMATED by applying official/searched
GCP pricing to that live config. No number is invented. Every figure states which it is.

All prices are in USD as published by Google (the pricing pages don't localize into CAD);
the billing account itself bills in CAD, so real invoiced amounts will differ slightly at
the prevailing exchange rate — this is a modelling estimate, not an invoice reconciliation.

---

## 1. Executive summary

The dominant cost driver is the **Vertex AI Agent Engine reasoning engine** (`sre-agent-gcp`,
Project A) running **2 always-on warm instances** (`min_instances = 2`, 4 vCPU / 8 GiB each) —
confirmed both in Terraform (`iac/agent/agent_engine.tf`) and via a live REST API read of the
deployed resource. That alone is estimated at **~$600–$610/month**, roughly 90% of total
estimated spend, and it runs whether or not anyone is using the agent (Agent Engine bills
provisioned compute capacity for warm instances, separate from per-token LLM cost, which this
repo already tracks separately). Everything else — the GKE Autopilot test cluster, Cloud Run
MCP service, Artifact Registry, 6 GCS buckets, Cloud NAT, BigQuery — is individually small
(under $75/month each, most under $5), so the total estimated range is
**~$608–$681/month**, with the GKE cluster management-fee free-tier eligibility (see §2) as
the main swing factor between the low and high end.

**This estimate is far above the two budget alerts actually configured on this billing
account** ("SRE Agent Monthly Budget - 20" = $20 CAD/month, "Personal GCP Trial Monthly
Budget" = $10 CAD/month — both VERIFIED via `gcloud billing budgets list`). Those are alert
*thresholds*, not real spend data, and this investigation had no way to see real spend (no
billing export). This gap is flagged, not resolved — see §6. It needs checking directly in
the GCP Billing Console's real cost breakdown before acting on the estimate below.

---

## 2. Cost breakdown table

| Resource | Project | VERIFIED live config | Official pricing rate (source) | Estimated monthly cost | Confidence |
|---|---|---|---|---|---|
| Vertex AI Agent Engine — `sre-agent-gcp` reasoning engine | A | `min_instances=2`, `max_instances=10`, `resourceLimits={cpu:4, memory:8Gi}` — VERIFIED in `iac/agent/agent_engine.tf` AND via live REST read (`GET reasoningEngines/7801582006105538560`) | $0.0864/vCPU-hr, $0.0090/GB-hr memory; 50 vCPU-hr + 100 GB-hr free/month (searched from cloud.google.com/vertex-ai/pricing — WebFetch on that page was blocked by page truncation, so this is WebSearch-sourced, not a direct doc read) | **~$604/month** (8 vCPU + 16 GiB warm 24/7, minus free allowance) | **Medium** — size is VERIFIED (live + IaC agree); rate is searched, not read directly from the official page |
| Vertex AI Agent Engine — `sre-agent-memory-bank` reasoning engine | A | No `deployment_spec`/`min_instances` set in Terraform (source-less memory-only engine) | Not determined — likely a different, lower-cost SKU (memory storage/API calls) but not confirmed | **Unknown — not estimated** | **Low / UNVERIFIED** |
| GKE Autopilot cluster `sre-test-cluster` — pod compute | B | VERIFIED live: `kubectl get nodes` → **0 nodes**; `kubectl get pods -A` → **20/20 pods Pending**, 0 Running. Cluster is Autopilot, tier `STANDARD` (not Enterprise), region us-central1, created 2026-06-25 | $0.0445/vCPU-hr, $0.0049225/GiB-hr memory, $0.0001389/GiB-hr ephemeral storage (Regular compute class, us-central1 — WebSearch-sourced, GKE pricing page also returned truncated) | **$0/month right now** (no scheduled/running pods to bill; would rise sharply the moment real workload pods get scheduled) | **Medium** — 0-node/0-running state is VERIFIED live; the "Pending pods aren't billed" claim is a reasonable INFERENCE from GKE's pod-based billing model, not a doc citation |
| GKE Autopilot cluster — cluster management fee | B | Same cluster, regional (us-central1) | $0.10/hr flat per cluster, all types (searched). A $74.40/month free-tier credit exists for "one zonal or Autopilot cluster per billing account" — but sources conflict on whether a **regional** Autopilot cluster (this one) qualifies | **$0–$73/month** (genuinely uncertain — not resolved) | **Low** — rate found via search, not an official-page read; eligibility question is UNRESOLVED, stated as a range not a number |
| Cloud Run `sre-k8s-mcp` | A | VERIFIED: `minScale=0`, `maxScale=3`, cpu=1000m, memory=512Mi, `containerConcurrency=80` | $0.000024/vCPU-sec, $0.0000025/GiB-sec, $0.40/million requests; scale-to-zero confirmed = $0 idle cost (searched, cloud.google.com/run/pricing page also truncated on fetch) | **~$0/month idle** + negligible usage-based cost | **Medium** — config VERIFIED, but no invocation-volume data exists to size the usage-based portion |
| Artifact Registry `sre-agent-repo` | A | VERIFIED: **3882.617 MB (~3.79 GiB)**, 25 images, **no `cleanup_policies` block** anywhere in `iac/agent/cloudrun_mcp.tf` (confirmed by reading the file — storage grows unbounded) | $0.10/GB/month beyond 0.5 GB free (searched, consistent across sources) | **~$0.33/month today**, rising indefinitely as more images are pushed with no cleanup policy | **High** — size VERIFIED directly, rate consistent across independent searches |
| GCS — 6 buckets (`tfstate`, `cluster-config`, `eval`, `evidence`, `query-jobs`, `_cloudbuild`) | A | VERIFIED via `gcloud storage du -s`: 561,318 + 934 + 10,229,368 + 3,461,981 + 0 + 1,042,932 bytes = **~15.3 MB total** (0.015 GB) | ~$0.02/GB/month regional (us-central1), ~$0.026/GB multi-region (US); 5 GB/month free tier (searched) | **$0.00/month** — ~300x under the free tier | **High** — size VERIFIED, and the total is so small the exact rate doesn't matter |
| VPC networking — Cloud NAT `sre-agent-nat` + router `sre-agent-router` | B | VERIFIED: NAT exists (`ALL_SUBNETWORKS_ALL_IP_RANGES`), 1 external IP reserved and `IN_USE` by the NAT (auto-allocated), **0 forwarding rules** (no load balancer) | $0.0014/hr per VM using the gateway + $0.045/GiB processed + $0.005/hr per external IP (searched, no official-page confirmation) | **~$3.65/month** (IP only, since 0 VMs currently use the gateway — 0 nodes on the cluster) | **Medium** — NAT existence VERIFIED, rate searched not doc-read, per-VM/data usage unmeasurable without billing export |
| Model Armor templates + floor setting | A | Existence stated in task context; **could not independently re-verify via `gcloud model-armor templates list`** — command returned `PERMISSION_DENIED` for the authenticated account, cause not investigated further | Reportedly token-based, ~2M free tokens/month/project then ~$0.10/million tokens (single non-Google third-party source — **not an official citation**) | **Not meaningfully estimable — likely low (usage-based, not idle)** | **Low / UNVERIFIED** — pricing source is weak, and live existence wasn't re-confirmed this pass |
| BigQuery `sre_agent_investigations` — storage | A | VERIFIED via `bq show`: 3 tables, `16,596,408 + 7,329,279 + 591,222` bytes = **~23.4 MB total**; all tables have a 90-day partition expiration (bounded growth, self-pruning) | $0.02/GB/month active storage, 10 GB/month free (searched) | **$0.00/month** | **High** — size VERIFIED, well under free tier regardless of exact rate |
| BigQuery — on-demand queries (dashboard) | A | Not measurable — no billing export, no query-job history pulled | $6.25/TiB scanned, 1 TiB/month free (searched) | **Likely $0/month** for personal-dashboard-scale use, but genuinely unknown | **Low / UNKNOWN** |
| Cloud Logging — ~15 log-based metrics, 12 alert policies | A | VERIFIED metrics exist (`gcloud logging metrics list`); ingested-byte volume **not measurable** without a billing export | 50 GiB/month free ingestion (context only, not a rate applied to a real number) | **Not estimated — UNKNOWN** | **Low / UNKNOWN** |

**Estimated total: ~$608–$681/month**, almost entirely (~89–90%) driven by the Agent Engine
line. The $0–$73 GKE management-fee uncertainty is the only other material swing.

---

## 3. Top cost drivers ranked

1. **Vertex AI Agent Engine `sre-agent-gcp` — 2 always-on warm instances (~$604/month, ~90% of the estimate).**
   `min_instances = 2` in `iac/agent/agent_engine.tf`, confirmed live via REST — 8 vCPU and
   16 GiB of memory are provisioned continuously, 24/7, regardless of whether any
   investigation is running. This is *hosting/compute* cost, separate from the per-token LLM
   cost this repo already tracks (`GEMINI_PRICE_INPUT`/`GEMINI_PRICE_OUTPUT` env vars on the
   same resource). It is the single number that matters most in this report — everything
   else combined is under $80/month.

2. **GKE cluster management fee uncertainty (~$73/month swing).**
   The cluster itself costs **$0 right now** for actual compute — VERIFIED 0 nodes, all 20
   system pods stuck `Pending`. But the flat $0.10/hr *per-cluster* fee applies "irrespective
   of ... topology" per GCP's own pricing model, and this cluster is regional, which several
   (non-official) sources say disqualifies it from the one-cluster-free-per-billing-account
   credit. This was not resolved with an official-page read — see §6.

3. **Artifact Registry with no cleanup policy — small today, will not stay small.**
   Confirmed no `cleanup_policies` block in `iac/agent/cloudrun_mcp.tf`. Currently only
   ~$0.33/month (3.79 GiB, 25 images from CI/CD git-SHA tags), but this is the one line in
   the table that grows monotonically with every future build — every other line is either
   flat, usage-based, or self-pruning (BigQuery's 90-day partition expiration).

Everything else in the table — Cloud Run (scale-to-zero), GCS (15 MB total), BigQuery
storage (23 MB, 90-day auto-expiry), Cloud NAT ($3.65 baseline), Model Armor (usage-based) —
is individually under $5/month and does not materially move the total.

---

## 4. What's free or negligible

- **GCS (6 buckets):** ~15 MB total, ~300x under the 5 GB/month free tier. $0.00.
- **BigQuery storage:** ~23 MB, ~430x under the 10 GB/month free tier. $0.00. Also
  self-bounded — every table has a 90-day partition expiration, so this never needs manual
  cleanup.
- **Cloud Run `sre-k8s-mcp`:** `minScale=0` confirmed live — genuinely $0 when idle, not just
  cheap.
- **GKE pod compute (right now):** $0 — 0 nodes provisioned, 0 running pods. This will change
  the moment the cluster does real work; it is not free by design, just currently idle.
- **Model Armor:** usage/token-based, not an idle cost — a demo-scale personal project is very
  unlikely to exceed the reported free monthly token allowance, though the pricing source
  itself is weak (see §6).

---

## 5. What reducing cost would require

This section is cost analysis only — no IaC design or changes are proposed here. It exists
because a separate, already-scoped disposable-environment Terraform lifecycle task in this
repo will need exactly this framing.

| Driver | Lever | Why it works |
|---|---|---|
| Agent Engine 2 warm instances (~$604/month) | Set `min_instances = 0` in `iac/agent/agent_engine.tf` when the agent isn't actively being demoed, or destroy/recreate the reasoning engine as part of the disposable-environment teardown | This is the only lever that touches the ~90%-of-total line item. `min_instances = 0` means Agent Engine no longer pays for warm idle capacity — Google's own model only charges for *provisioned* compute; at 0 provisioned instances there is nothing to bill for that SKU (still subject to normal cold-start latency on next use, a functional tradeoff not a cost one) |
| GKE cluster management fee ($0–$73/month) | Delete the cluster entirely during teardown (`gcloud container clusters delete` / Terraform destroy of the cluster resource) rather than leaving it running idle | The $0.10/hr fee is per-cluster and flat regardless of usage — a cluster sitting at 0 nodes still may be charged this fee (see §6, unresolved). Deleting it during teardown removes the exposure entirely rather than leaving an ambiguous idle charge running |
| Artifact Registry unbounded growth | Add a `cleanup_policies` block to `google_artifact_registry_repository` in `iac/agent/cloudrun_mcp.tf` (e.g., keep last N tagged images, delete untagged after N days) | Currently absent — confirmed by reading the file. This is the only line item with no natural ceiling; every rebuild adds ~130–300 MB permanently |
| Cloud NAT baseline (~$3.65/month) | Delete the NAT gateway + router along with the cluster during teardown, since NAT exists to give cluster/VPC workloads egress — no cluster, no need for NAT | Small on its own, but it's pure waste once the cluster it serves is gone |

---

## 6. What could not be determined

- **No BigQuery billing export exists** — this is the primary limitation of this whole
  report. Every dollar figure above is a *model*, built from live resource sizing × official
  or searched pricing, never from actual observed spend. Recommend setting up a billing
  export (BigQuery-linked) as the single highest-value follow-up — it would replace every
  ESTIMATED/UNKNOWN row above with a VERIFIED one.
- **GKE cluster management-fee free-tier eligibility for a *regional* Autopilot cluster** —
  genuinely unresolved. Direct `WebFetch` reads of `cloud.google.com/kubernetes-engine/pricing`
  were blocked by page-content truncation in this environment (the page is likely
  JS-rendered); all rates in this report came from `WebSearch` synthesis of that page instead
  of a direct read. This is the single most valuable thing to verify next — it's a ~$73/month
  swing and should be resolvable by opening the pricing page directly in a browser.
- **Actual pod resource requests if/when the cluster is used for a real investigation** — not
  measured, since no user workload was running at inspection time (all 20 pods observed were
  system/managed pods, all `Pending`).
- **Model Armor exact pricing** — sourced from a single non-Google third-party site, not an
  official page. Existence of the templates/floor setting (asserted in task context) was
  **not** re-verified live this pass: `gcloud model-armor templates list --project=sreagent-t2-demo`
  returned `PERMISSION_DENIED` for the authenticated `ashmin.sub@gmail.com` account — cause
  not investigated (could be a real permission gap, or the wrong API surface/flag for this
  gcloud version). Worth a follow-up if Model Armor cost/config needs confirming.
- **Cloud Logging ingested-byte volume** — cannot be computed without the billing export.
  ~15 log-based metrics and 12 alert policies exist (confirmed), but their actual log ingest
  volume, and whether it's inside or outside the 50 GiB/month free tier, is unknown.
- **BigQuery on-demand query volume** for the investigation dashboard — no query-job history
  was pulled; likely negligible for personal-scale use but not measured.
- **The gap between this ~$608–$681/month estimate and the $10–$20 CAD/month budget alerts**
  configured on this billing account — flagged in §1, not resolved. Possible explanations
  include promotional/trial credits absorbing the cost, the estimate being wrong (most likely
  culprit: the Agent Engine rate, which is WebSearch-sourced not doc-verified), or alerts
  having fired without being acted on. This needs a direct look at the GCP Billing Console's
  real cost breakdown, which this investigation had no access path to (no billing export).
- **Vertex AI Agent Engine pricing rate itself** ($0.0864/vCPU-hr, $0.0090/GB-hr) — WebFetch on
  `cloud.google.com/vertex-ai/pricing` failed (`maxContentLength exceeded` — the page is very
  large). The rate came from WebSearch synthesis citing that page, not a direct read. Given
  this is the number that drives ~90% of the whole estimate, it is the single highest-value
  fact to re-verify directly against the official page before acting on this report.
