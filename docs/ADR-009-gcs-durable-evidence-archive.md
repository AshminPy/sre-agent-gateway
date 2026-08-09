# ADR-009: GCS as the durable evidence archive, not just LangGraph state

Status: Accepted

## Context

`AgentState` — the LangGraph state object every node reads/writes — exists
only for the duration of one `graph.invoke()` call. If raw tool output and
the full evidence trail lived only there, every investigation's underlying
data would vanish the moment the graph finished: no way to audit what the
agent actually looked at, re-derive a claim's support after the fact, or
investigate a disputed RCA later. Separately, raw MCP tool output can contain
sensitive fields (tokens, IPs, emails) that should never sit unredacted in
LLM-visible state or be repeatedly re-sent into prompts.

## Decision

Split evidence into two forms and store the durable one outside graph state,
in Cloud Storage:

- **Raw MCP result** — the literal JSON/text a tool call returns, redacted
  for secrets/PII, written to `gs://{EVIDENCE_BUCKET}/{run_id}/{evidence_id}.json`
  via `write_evidence()` (`agent/gcs_client.py:27-61`). This is a deliberate
  design rule stated in the code itself (`agent/state.py:74-78`, "Raw MCP
  output NEVER enters state — lives in GCS only") — raw output never becomes
  part of `AgentState` or any LLM prompt.
- **Structured/normalized evidence** — the compressed output of
  `evidence_extractor`'s LLM call (a handful of key-facts strings, a summary,
  and a `raw_ref` pointing back at the GCS object) — this is what actually
  populates `evidence_store` in `AgentState` and what the model sees in later
  prompts.
- Redaction (`redact()`, `agent/gcs_client.py:109-153`) strips email
  addresses, IPv4 addresses, bearer tokens, and any JSON field whose key
  looks like `password`/`token`/`secret`/`key`/`credential`, applied
  consistently before both the GCS write and any LLM context — not a
  sometimes-control.
- Retention is set via Terraform lifecycle rules
  (`iac/agent/buckets.tf`): the evidence bucket at 90 days, and a separate
  eval bucket holding full RCA records at 365 days — both versioned, both
  `prevent_destroy = true`.
- Writes retry up to 2 attempts, 1-second gap; on total failure, the write
  function returns a sentinel string instead of raising, and the evidence
  item is marked `gcs_write_failed=True` — the investigation continues rather
  than aborting on a storage hiccup.

## Alternatives Considered

- **Keep everything, raw and structured, only in `AgentState`** — rejected
  because state doesn't survive past a single `graph.invoke()` call; there
  would be no way to audit an investigation after the fact, re-open a
  disputed RCA, or support the citation-checking mechanism
  (`_ground_claim()`) that needs a stable, addressable evidence ID space
  outside any one run's transient memory.
- **Send raw, unredacted tool output into the LLM prompt directly, skip a
  separate storage step** — rejected on both a cost and a safety basis: raw
  Kubernetes tool output can be large and can contain secrets/PII, and
  feeding it directly into every prompt would both bloat context and risk
  leaking sensitive data into the model's input. Compressing to structured
  facts first, with the raw form addressable-but-not-inline via `raw_ref`,
  keeps prompts small while still letting `rca_builder`'s "enriched digest"
  logic re-read full raw data when the compressed summary isn't enough.

## Reason

GCS gives evidence a lifetime independent of any single investigation's
in-memory state, which is exactly what auditability and later re-verification
require: every evidence write, tool call, and failure carries the `run_id`,
so a reviewer can reconstruct exactly what the agent looked at for any given
investigation (see [Logs](operations/logging.md#where-do-i-see-one-complete-investigation)).
Splitting raw-vs-structured evidence and keeping raw output out of
`AgentState` entirely is also what keeps the read-only, defense-in-depth
security posture (see [ADR-005](ADR-005-read-only-by-design.md)) from being
undermined by an unrelated leak vector — a secret making it into an LLM
prompt would be a real incident regardless of how well the read-only tool
boundary held.

## Tradeoffs

- Two extra hops per evidence item (write to GCS, then compress via LLM into
  `evidence_store`) versus a design that only used state — a real latency and
  complexity cost, accepted for the durability/auditability it buys.
- Freshness checking is a known, documented limitation of the current shape:
  there's a `freshness` component in the deterministic completeness score,
  but it's currently a single investigation-level proxy, not a true
  per-evidence-item timestamp check — no per-evidence timestamp field exists
  yet. This also blocks a fully real `time_correlation` component in
  Root-Cause Confidence (see [ADR-008](ADR-008-confidence-not-accuracy.md)).
- A GCS write failure doesn't halt the investigation — a deliberate
  availability-over-strict-consistency choice — but it does mean an
  investigation can complete with some evidence items marked
  `gcs_write_failed=True`, i.e. referenced in state but not actually durable
  in the archive. Callers needing a hard durability guarantee should check
  that flag rather than assume every evidence ID has a live GCS object.

## Related ADRs

- [ADR-006: Evidence before RCA](ADR-006-evidence-before-rca.md)
- [ADR-005: Read-only by design](ADR-005-read-only-by-design.md)
