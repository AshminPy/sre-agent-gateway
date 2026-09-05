# AI SRE Agent — Investigation Dashboard

Read-only observability/reporting on top of the SRE Agent's real, existing
telemetry. Built 2026-09-05. Backing infrastructure: `iac/observability/`.
Field-level schema and source mapping: `schema.md`. Looker Studio build
notes: `looker-studio/DASHBOARD_SPEC.md`.

## Architecture

```
agent/nodes/rca_builder.py  ─┐
agent/main.py (crash path)  ─┴─▶ "sre-agent-investigations" log ─┐
Model Armor sanitize_operations log                              ├─ Cloud Logging sinks ─▶ BigQuery raw tables ─▶ views ─▶ Looker Studio
Agent Gateway gateway_requests log                                ┘
```

Three Cloud Logging sinks (`iac/observability/investigation_dashboard.tf`),
each writing to its own raw, day-partitioned BigQuery table. Three views
(`iac/observability/views.tf`) normalize those raw tables into the actual
contract Looker Studio queries: `v_investigations` (one row per run),
`v_model_armor_activity` and `v_gateway_activity` (aggregate/time-series
only — neither log carries a run_id, so no per-investigation join is
possible or attempted for either).

No compute, no always-on resources. A BigQuery dataset and Cloud Logging
sinks are pure metadata/routing constructs with no idle cost.

## Ownership boundary

- `iac/agent/` — the agent itself, unchanged in behavior by this work except
  two small, additive telemetry fields (`environment`, and the crash-terminal
  event — see `schema.md`).
- `iac/observability/` — this dashboard's own backend (dataset, sinks, views,
  IAM). Separate Terraform state, separate lifecycle, can be destroyed
  without touching the agent stack.
- Looker Studio report itself — created through the browser (no supported
  Terraform/IaC mechanism for Looker Studio reports as of this build); see
  `looker-studio/DASHBOARD_SPEC.md` for what's manual vs. automatable.

## Filters and refresh

Global filters: date range (default last 7 days), cluster, environment,
model. Data freshness target: a few minutes (Cloud Logging → BigQuery sink
latency is typically under a minute per Google's own docs; Looker Studio's
own refresh interval is set deliberately conservative — this is operational
reporting, not a sub-second console, and an aggressive refresh only spends
BigQuery query quota for no benefit at this data volume).

## Security / IAM

- Dataset-level `roles/bigquery.dataViewer` for the dashboard's own
  viewer/builder account — not project-wide, not public.
- Project-level `roles/bigquery.jobUser` for the same account (BigQuery's
  query-execution permission is project-scoped, not dataset-scoped; this
  does NOT grant access to any other dataset's data).
- Each Cloud Logging sink's own writer identity gets `roles/bigquery.dataEditor`
  scoped to this ONE dataset only.
- No secrets, tokens, Kubernetes credentials, or raw Secret contents flow
  into any of the three log sources by design — none of `sre-agent-investigations`,
  the Model Armor log, or the Agent Gateway log ever carries request/response
  bodies or credential material.

## Cost

See the PR/final report for the exact estimate at the time of build. Expected
to be effectively $0/month at this project's real investigation volume —
BigQuery storage and query costs are both well under Google's free tier at
this scale, and Cloud Logging sinks themselves are free.

## Troubleshooting

- **A dashboard field shows nothing / view creation fails on a field**: check
  `schema.md`'s "known current gap" section — BigQuery only materializes a
  JSON field into the schema once at least one log entry has a non-null
  value for it. Check `bq show --format=prettyjson PROJECT:DATASET.TABLE`
  for whether the field actually exists yet.
- **A new investigation doesn't show up**: Cloud Logging → BigQuery sink
  latency is usually under a minute, but can be several minutes on a
  freshly-created table (Google's own documented behavior). Wait, then
  re-check with `bq query` directly against the raw table before assuming
  something is broken.
- **Adding a new MCP/cluster/data source**: if it writes to a NEW log name,
  it needs its own `google_logging_project_sink` in
  `iac/observability/investigation_dashboard.tf` (copy the pattern of the
  three existing sinks) plus its own `google_bigquery_dataset_iam_member`
  for that sink's writer identity, plus a new/extended view in `views.tf`.
  If it writes to an EXISTING log (e.g. a new MCP tool call inside the same
  `sre-agent-investigations` log), no new sink is needed — just extend
  `v_investigations`'s `SELECT` list.

## How to recreate this dashboard from scratch

1. `cd iac/observability && terraform init -backend-config="bucket=<tfstate bucket>"`.
2. `terraform apply -var="project_a_id=<project>" -var="dashboard_viewer_email=<your email>"`.
3. Trigger at least one real investigation so the raw tables' schemas
   materialize (Cloud Logging sinks do not backfill history).
4. Verify the raw table names Cloud Logging actually created match what
   `views.tf`'s `FROM` clauses expect (`bq ls PROJECT:DATASET`) — if a log ID
   is ever renamed, update `views.tf` to match; there is no way to control
   this from Terraform.
5. Build the Looker Studio report per `looker-studio/DASHBOARD_SPEC.md`,
   pointing each chart's data source at the three views, never the raw
   tables directly.
