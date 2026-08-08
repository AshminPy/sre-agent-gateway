# Executive Overview

> **Implementation Status:** Reference page.
> **Last Verified:** 2026-08-08
> **Owner:** SRE Agent platform team.

## One paragraph

The SRE AI Agent is an automated Kubernetes incident investigator running on Google Cloud (Vertex AI Agent Engine). Given an incident description, it gathers read-only diagnostic evidence from the affected cluster, reasons over it with Gemini, and produces a written root-cause report with a code-verified confidence assessment. It cannot modify anything in Kubernetes — no delete, create, patch, exec, or scale capability exists anywhere in its design. A human reviews the report and decides on any remediation.

## Current state, honestly

**Working and live-verified**: the core investigation loop against GKE clusters, via Google's managed GKE Remote MCP, fronted by Agent Gateway with an Agent Identity (no static credentials anywhere).

**Working but with real, known gaps**: confidence scoring is a genuine deterministic mechanism, but its specific thresholds are explicitly labeled uncalibrated; observability exists but has a confirmed metric-doubling issue; alerting covers 11 of 14 originally-planned scenarios.

**Not working today, despite existing in Terraform/code**: the fallback Kubernetes-tool path (custom MCP), on-prem/non-GKE cluster support, and Model Armor content-safety inspection. Each of these has real code and real Terraform behind it, but none is actually functioning in the live deployment — see [Risks and Limitations](risks-and-limitations.md) for the full list with evidence.

## Why this matters for decision-makers

This system is genuinely safe by design (read-only, fully audited, no autonomous action capability) — that part of the story is solid and well-evidenced. What it is *not* yet is a fully mature, fully calibrated, fully alerted production platform — several real gaps exist and are documented plainly rather than glossed over. Use [Risks and Limitations](risks-and-limitations.md) and the [Executive FAQ](executive-faq.md) for the specific, honest picture before making commitments based on this system's current capabilities.

---

**Related pages:** [Executive FAQ](executive-faq.md) · [Risks and Limitations](risks-and-limitations.md)
