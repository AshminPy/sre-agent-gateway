# Runbook: Evidence, Memory, and PagerDuty Integration Failures

> **Last Verified:** 2026-08-08 · **Owner:** SRE Agent platform team

## 24. GCS evidence write failure

**Symptom**: `SRE Agent — Evidence Storage Failures` alert fires; RCA shows `evidence_storage_ok: false`.

**How to verify**: `logName="projects/sreagent-t2-demo/logs/sre-agent-evidence-storage-failures"` — check the exact error (permission denied vs. bucket-not-found vs. transient).

**Resolution**: writes retry once automatically (1-second gap) before this fires — so this alert means both attempts failed. Check the evidence bucket's IAM (`iac/agent/iam.tf` — `roles/storage.objectCreator` on the evidence bucket) and that the bucket itself still exists (`prevent_destroy=true` in Terraform, so it shouldn't have been deleted, but verify).

**Note**: the investigation continues even when this fails — it's not a hard-stop failure, just a broken audit trail for that specific evidence item.

## 25. Long-term memory (Memory Bank) failure

**Symptom**: memories aren't being recalled/written; no dedicated alert exists (see [Alerting gaps](../operations/alerting.md#gaps)).

**How to verify**: check `MEMORY_BANK_RESOURCE` env var is still set on the live engine (`iac/agent/agent_engine.tf` wires this from the companion `memory_bank` reasoning engine's ID); check for `memory_bank_recall` log events — if they've stopped appearing for clusters that previously had recalls, something's wrong.

**Resolution**: if the env var/resource reference is intact, the fallback in-process memory list (max 20, non-durable) is still functioning as a degraded mode — investigations continue working, just without cross-restart memory. Escalate to platform team to diagnose the Memory Bank resource itself if the durable path is down.

**Note**: writes only happen for `confidence_band=="auto"` RCAs by design (see [Memory](../architecture/memory.md)) — don't mistake the *selective* write gate for a failure.

## 26. PagerDuty trigger failure

**N/A — no PagerDuty integration exists in this codebase today.** See [Reliability](../governance/reliability.md). If this runbook entry is ever needed, it means the integration has been built since this doc was last verified — update this page.

## 27. Malformed PagerDuty incident

**N/A — same as above.**

---

**Related pages:** [Evidence Architecture](../architecture/evidence-architecture.md) · [Memory](../architecture/memory.md) · [Reliability](../governance/reliability.md)
