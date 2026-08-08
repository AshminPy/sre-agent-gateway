# Operations Overview

> **Implementation Status:** Reference page — see linked pages for status of each area.
> **Last Verified:** 2026-08-08
> **Owner:** Run/Ops team (once handed off).

This is the entry point for Part 2 — day-to-day operation of the deployed SRE Agent. If Part 1 explained *how it works*, this part explains *how you run it*.

## What you're operating

One live deployment: GCP project `sreagent-t2-demo`, Vertex AI Agent Engine resource `sre-agent-gcp` (plus a companion `sre-agent-memory-bank` engine), fronted by Agent Gateway, reaching Kubernetes via Google-managed GKE Remote MCP against a target cluster in project `sreagent-demo` (project B). See [Component Ownership](../governance/ownership-raci.md) for the full inventory.

## Where to start each day

[Daily Health Check](daily-health-check.md).

## Where things are

| Need to... | Go to |
|---|---|
| Understand a metric | [Observability](observability.md) |
| Find logs for one investigation | [Logging](logging.md) |
| Understand a trace | [Tracing](tracing.md) |
| Check what alerts exist | [Alerting](alerting.md) |
| Fix a specific failure | [Troubleshooting Runbooks](../runbooks/) |
| Deploy a change | [Updating the Agent](deployment.md) |
| Undo a bad change | [Rollback](rollback.md) |
| Understand the Terraform layout | [Terraform / Infrastructure Management](terraform.md) |
| Handle an outage | [Disaster Recovery](disaster-recovery.md) |

## The one rule that matters most operationally

**Every `terraform apply` that touches the reasoning engine wipes an out-of-band binding that isn't tracked in Terraform state**: the gateway attachment. `scripts/attach_gateway_to_engine.sh` must be re-run after any such apply. CI does this automatically (see [CI/CD](deployment.md#cicd)); if you ever apply manually, do not skip this step. See [Agent Gateway](../architecture/agent-gateway.md#how-certificatestls-work--the-atomic-patch-mechanism) for why.

---

**Related pages:** [Component Ownership](../governance/ownership-raci.md) · [Daily Health Check](daily-health-check.md)
