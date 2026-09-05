# AI SRE Agent — Investigation Dashboard — Looker Studio build spec

**Status: not yet built.** No browser/UI-automation tool was available in the
session that built the BigQuery backend (`iac/observability/`) — this is a
session tooling gap, not a Looker Studio permission or product limitation.
This document is the exact, ready-to-execute build sheet so the report can be
created by hand (or by an agent with browser access) in roughly 15–20
minutes, with zero guessing about data sources, fields, or filters.

Data source verified live and correct as of 2026-09-05 — see
`observability/dashboard/schema.md` for the full field mapping and
`PHASE1_EVIDENCE_LOG.md`-style verification already performed on every view
this spec references.

## 0. Connect the data sources (do this once)

In Looker Studio (lookerstudio.google.com):
1. **Create** → **Data source** → **BigQuery** connector.
2. Project: `sreagent-t2-demo`. Dataset: `sre_agent_investigations`.
3. Add three data sources, one per view — **never connect directly to a raw
   sink table** (`sre_agent_investigations`, `modelarmor_googleapis_com_sanitize_operations`,
   `networkservices_googleapis_com_gateway_requests`), only to:
   - `v_investigations`
   - `v_model_armor_activity`
   - `v_gateway_activity`
4. For each, confirm the auto-detected field types: `event_timestamp` should
   be DATE/TIME (Looker Studio usually infers this correctly from a BigQuery
   TIMESTAMP column — if it comes in as text, manually set the type to
   Date & Time before building any time-series chart).
5. Your Google account needs `roles/bigquery.dataViewer` on the dataset and
   `roles/bigquery.jobUser` on the project — both already granted by
   `iac/observability/investigation_dashboard.tf` to whichever email was
   passed as `dashboard_viewer_email`.

## 1. Report shell

- Name: **AI SRE Agent** / subtitle **Investigation Dashboard** / tagline
  **Kubernetes • GCP • Multi-Cluster** (a text box under the title, matching
  the reference mockup).
- Theme: Looker Studio's built-in **"Simple (Light)"** theme is the closest
  native match to the requested light Google/cloud aesthetic. Set the report
  background to white, and use Looker Studio's native **Style → Rounded
  corners** option on every scorecard/chart to approximate the mockup's
  pastel rounded cards (Looker Studio does not support custom CSS/border-
  radius beyond this built-in toggle — see §7 limitations).
- Pages, in this exact order (per the approved 4-page scope; add page 5 only
  if real data justifies it, see §6): **Overview**, **Investigations**
  (Investigation Explorer), **Reliability & Security**, **Cost & Performance**.
- Top-of-every-page filter controls (Looker Studio "Filter control" widgets,
  placed in a header row present on all 4 pages — use "Add to all pages"):
  - Date range control → default **Last 7 days**.
  - Dropdown filter on `v_investigations.cluster`.
  - Dropdown filter on `v_investigations.environment`.
  - Dropdown filter on `v_investigations.model`.
- Bottom-left of the Overview page: a small text/data-source info box stating
  "Data source: Cloud Logging → BigQuery. Looker Studio (free). Last updated:
  [insert Looker Studio's own built-in Report freshness indicator, or a
  scorecard on `MAX(event_timestamp)`]."

## 2. Page 1 — Overview

**KPI scorecards** (6, matching the mockup's row) — all from `v_investigations`,
filtered to the active date range:
| Card | Metric | Comparison |
|---|---|---|
| Total Investigations | `COUNT(run_id)` | vs previous period (Looker Studio scorecard's built-in "Comparison date range") |
| Success Rate | Calculated field `COUNTIF(status="success") / COUNT(run_id)`, format as % | vs previous period |
| Avg Duration | `AVG(total_latency_s)` — already exposed in `v_investigations`, format as duration (Looker Studio's duration formatting expects seconds, which is exactly what this field is) | vs previous period |
| Avg Tokens | `AVG(tokens_total)` — Looker Studio's `AVG()` already skips NULLs by default, so crash rows with unknown tokens are correctly excluded without any extra filter | vs previous period |
| Avg Estimated Cost (USD) | `AVG(estimated_llm_cost_usd)` — same NULL-skipping behavior | vs previous period |
| Model Armor Blocks | From `v_model_armor_activity`: `COUNTIF(dashboard_state="blocked")` | vs previous period |

**Do not** build "Avg Tokens"/"Avg Cost" as `SUM(x)/COUNT(run_id)` — that
divides by the total row count including crash rows with no real metric,
silently reintroducing the exact fabricated-zero problem the telemetry fix
just eliminated. Use `AVG()` directly (NULL-safe) or, if a calculated field
is needed, `SUM(x)/COUNTIF(partial_metrics_available)`.

**Charts** (all from `v_investigations` unless noted):
1. **Investigations Over Time** — time-series/stacked bar, `event_timestamp`
   (day granularity) on X, series = `status`. Only `"success"`/`"error"` exist
   today (rca_builder's own two-value enum) — there is no `"blocked"` status
   value in `v_investigations` (a Model-Armor-level block is not the same
   thing as an investigation-level status; see §Accuracy rules below). Label
   the legend "Success" / "Error", not "Successful / Failed / Blocked" —
   matching real data, not the mockup's exact wording.
2. **RCA Categories** — donut chart on `rca_category_normalized`, `COUNT(run_id)`
   as the metric, center label = total count (Looker Studio donut charts
   support a center metric natively). Real categories today will be whatever
   the live data actually produced — do not pre-populate legend entries for
   categories with zero real occurrences.
3. **Model Usage** — horizontal bar, dimension `model`, metric `COUNT(run_id)`.
   **Will show only Gemini models** (`gemini-2.5-pro` and/or `gemini-2.5-flash`
   if ever used) — do not add Claude/other model entries; no adapter for them
   exists in this deployment (verified against `agent/llm/registry.py`).
4. **Token Usage Trend** — time-series, `event_timestamp` on X, two metrics:
   `SUM(tokens_input)`, `SUM(tokens_output)` (or `AVG` if per-investigation
   trend is preferred over daily total — either is defensible, pick one and
   label it).
5. **Investigation Duration** — time-series, two metrics: a P50 and P95
   calculated field on `total_latency_s` (Looker Studio supports `PERCENTILE(field, 50)`
   / `PERCENTILE(field, 95)` as calculated-field functions).
6. **Model Armor Activity** — from `v_model_armor_activity`: stacked bar,
   `event_timestamp` (day) on X, series = `dashboard_state`
   (`allowed`/`detected`/`blocked`/`error`). **Do not merge "detected" and
   "blocked" into one series** — they are materially different (floor-setting
   detections are inspect-only and were never blocking anything).

## 3. Recent Investigations table (bottom of Overview, or its own section)

Table from `v_investigations`, sorted by `event_timestamp` DESC, columns in
this order: `run_id` (set as the table's "link" field → `agent_log_link`, so
the cell itself is clickable in Looker Studio's native table link support),
`event_timestamp`, `status` (use a Looker Studio conditional-formatting rule:
green pill for `success`, red for `error` — there is no `blocked` value at
the investigation level, see above), `cluster`, `environment`,
`rca_category_normalized`, `model`, `tokens_total`, `estimated_llm_cost_usd`,
`total_latency_s`, `tool_call_count`, `agent_log_link` (as a plain text/link
column too, in case the row-link approach above isn't wanted).

**Gateway/MCP log columns**: per the explicit accuracy rule, these are
**contextual, not exact-run links** — Looker Studio's table doesn't support
three independently-labeled inline links in one cell cleanly (see §7). The
practical native option: add three more link-type columns
(`agent_log_link`, and two calculated-field URLs built the same way
`v_investigations.agent_log_link` was — one for a Cloud Run/Gateway log
console query scoped to this run's `event_timestamp` ± a few minutes, not
`run_id`), each clearly labeled in its column header as "Agent (exact)",
"Gateway (~time)", "MCP (~time)" so the imprecision is visible in the UI
itself, not hidden.

## 4. Page 2 — Investigation Explorer

Filters (Looker Studio filter controls, this page only): `run_id` (search
box), date, `cluster`, `model`, `mcp_source`, `status`, `rca_category_normalized`.

Detail table/section, one row expandable or a single wide table, all from
`v_investigations`: `run_id`, `event_timestamp`, `total_latency_s`, `cluster`,
`cluster_type`, `mcp_source`, `model`, `tokens_input`, `tokens_output`,
`tokens_total`, `estimated_llm_cost_usd`, `tool_call_count`,
`evidence_count`, `investigation_completeness_score`,
`root_cause_confidence_score`, `loop_exit_reason`, `rca_category_normalized`,
`incident_type_raw`, `trace_id`, `agent_log_link`.

**Do not** add a per-row Model Armor or Gateway result column here bound to
this run's exact time — per the explicit accuracy rule, these two sources
have no `run_id` and must not be presented as if correlated to one
investigation. If a security summary is wanted on this page, use a small
"Gateway/Model Armor activity in this time window" mini-chart (from the
other two views, filtered to `event_timestamp` near the selected run) with a
visible label like "activity near this run (not exact-correlated)".

## 5. Page 3 — Reliability & Security

From `v_investigations` (aggregate over the filtered period):
- Total errors: `COUNTIF(status="error")`.
- Investigations with zero evidence: `COUNTIF(evidence_count=0)` — exclude
  `partial_metrics_available=false` rows from this count (unknown ≠ zero).
- Investigations with low completeness / low confidence: `COUNTIF(investigation_completeness_score < 0.5)`,
  same for `root_cause_confidence_score` — pick a threshold and label it
  explicitly in the chart title (e.g. "Completeness < 0.5").
- Top `loop_exit_reason` values — bar chart, dimension `loop_exit_reason`,
  metric `COUNT(run_id)`. `"runtime_exception"` here is exactly the
  crash-terminal-event rows — a genuinely useful, now-visible signal that
  didn't exist before this session's telemetry fix.

From `v_gateway_activity` (aggregate/time-series only, explicitly labeled as
such in a section header, e.g. "Gateway activity — aggregate, not
per-investigation"):
- ALLOW/DENY trend over time: time-series, `event_timestamp` on X, series =
  `overall_result`.
- Top denied destinations (if any `DENIED` rows exist): bar chart,
  `destination_hostname`, filtered to `overall_result="DENIED"`.

From `v_model_armor_activity` (same aggregate-only labeling):
- Detections vs blocks over time: time-series, series = `dashboard_state`,
  restricted to `detected`/`blocked` (exclude `allowed` from this specific
  chart to keep it focused on security-relevant events).
- Mechanism breakdown: `mechanism` (`floor_setting` vs
  `app_or_gateway_content_authz`) × `dashboard_state`.

Link back to affected investigations: since these two sources have no
run_id, "link back" means a time-filtered jump to the Investigation Explorer
page (Looker Studio supports page-link buttons/URL navigation with a
parameter carrying the clicked timestamp) — not a literal per-row link to a
specific `run_id`. Label the link/button accordingly, e.g. "View investigations
around this time," not "View this investigation."

## 6. Page 4 — Cost & Performance

From `v_investigations`:
- Total tokens (period): `SUM(tokens_total)`.
- Input/output token trend: time-series, `SUM(tokens_input)`, `SUM(tokens_output)`.
- Avg tokens/run: `AVG(tokens_total)` (NULL-safe, per §2's rule).
- Estimated LLM cost/run: `AVG(estimated_llm_cost_usd)` — label exactly
  **"Estimated LLM Cost"**, never "Actual Cost" (per the explicit labeling
  rule — the real GCP bill includes Agent Engine/Cloud Run/Logging/BigQuery
  costs this number doesn't capture).
- Estimated LLM cost/day: time-series, `SUM(estimated_llm_cost_usd)` per day.
- Cost by model: bar chart, `model` × `SUM(estimated_llm_cost_usd)`.
- Cost by cluster: bar chart, `cluster` × `SUM(estimated_llm_cost_usd)`.
- P50/P95 duration: reuse the calculated fields from Overview.
- Investigations by model: reuse Overview's Model Usage chart, or a
  simplified count-only version here if the page needs a lighter version.

## Page 5 — Clusters/MCP (optional — build only if justified)

At the time of this spec, the real dataset has investigations against
exactly the clusters exercised during Phase 1 testing (`sre-lab`,
`sre-test-cluster`, and whatever else appears live) — check
`SELECT DISTINCT cluster FROM v_investigations` before deciding whether a
dedicated page adds value over the existing per-cluster breakdowns already
on pages 1/3/4. If ≥3 distinct real clusters exist with meaningful volume
(not 1-2 test entries), build: cluster, type (`cluster_type`), `mcp_source`,
investigation count, success rate, avg duration, last investigation
timestamp, error count — all straightforward `v_investigations` aggregations
grouped by `cluster`. If real data doesn't justify it yet, skip this page
rather than build an empty/near-empty one.

## 7. Known Looker Studio product limitations vs. the mockup

- **Custom icon sidebar navigation**: Looker Studio's native page navigation
  is a simple page-list panel, not a custom icon rail. Closest native
  approximation: name each page with a short label and rely on Looker
  Studio's own page-navigation UI; a fully custom sidebar would require an
  embedded custom visualization (community connector/visualization), out of
  scope for a native build.
- **Rounded pastel KPI cards with a trend arrow + colored pill in one
  compact widget**: achievable via Looker Studio's native scorecard style
  options (background color, rounded corners, and the built-in comparison
  arrow), but the exact visual density of the mockup (icon + rounded pill +
  arrow + two-line layout) is a close approximation, not a pixel-identical
  reproduction.
- **Three independently-colored inline log links in one table cell**: not
  natively supported as three separate clickable elements in a single
  Looker Studio table cell. Workaround used in this spec (§3): separate
  link-type columns per log source, each clearly labeled.
- **Donut chart with a large center number**: natively supported, matches
  the mockup closely.
- These are the only known material gaps between the mockup and a native
  Looker Studio build; everything else in this spec is a direct match.

## 8. Report ownership / access

Share the finished report with the same `dashboard_viewer_email` already
granted BigQuery access in Terraform (view-only Looker Studio sharing,
matching the least-privilege IAM already set up) — do not set the report to
"Anyone with the link" or public.

## 9. Once built

Fill in below and move this section's content into the final report:
- Looker Studio report URL: _____
- Screenshot of each page → save under `observability/dashboard/looker-studio/screenshots/`.
