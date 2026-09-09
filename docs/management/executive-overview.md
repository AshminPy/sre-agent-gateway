# Executive Overview

> **Implementation Status:** Reference page.
> **Last Verified:** 2026-09-07 (custom-MCP/on-prem and Model Armor status corrected — see [Current State](CURRENT-STATE.md))
> **Owner:** SRE Agent platform team.

## One paragraph

The SRE AI Agent is an automated Kubernetes incident investigator running on Google Cloud (Vertex AI Agent Engine). Given an incident description, it gathers read-only diagnostic evidence from the affected cluster, reasons over it with Gemini, and produces a written root-cause report with a code-verified confidence assessment. It cannot modify anything in Kubernetes — no delete, create, patch, exec, or scale capability exists anywhere in its design. A human reviews the report and decides on any remediation.

## Current state, honestly

**Working and live-verified**: the core investigation loop against GKE clusters, via Google's managed GKE Remote MCP, fronted by Agent Gateway with an Agent Identity (no static credentials anywhere). The fallback Kubernetes-tool path (custom MCP) and Connect Gateway-based non-GKE cluster access are also live production infrastructure now, not just code — a real Agent → Agent Gateway → custom Cloud Run MCP → Connect Gateway → `kind` cluster (`sre-lab`) investigation path has run dozens of successful real investigations (2026-09-04 through 2026-09-07).

**Working but with real, known gaps**: confidence scoring is a genuine deterministic mechanism, but its specific thresholds are explicitly labeled uncalibrated; observability exists but has a confirmed metric-doubling issue; alerting covers 11 of 14 originally-planned scenarios. Model Armor content-safety inspection is active (a CONTENT_AUTHZ extension at Agent Gateway inspects and can block request/response traffic; floor settings additionally inspect, not yet block, for malicious URIs) but has one permanent, disclosed platform limitation: Google's Streamable HTTP transport never invokes response-body inspection for MCP tool responses.

See [Risks and Limitations](risks-and-limitations.md) for the full, current list with evidence — as of 2026-09-07 none of this system's major capabilities are simply "not working" the way an earlier version of this page described.

## Why this matters for decision-makers

This system is genuinely safe by design (read-only, fully audited, no autonomous action capability) — that part of the story is solid and well-evidenced. What it is *not* yet is a fully mature, fully calibrated, fully alerted production platform — several real gaps exist and are documented plainly rather than glossed over. Use [Risks and Limitations](risks-and-limitations.md) and the [Executive FAQ](executive-faq.md) for the specific, honest picture before making commitments based on this system's current capabilities.

---

**Related pages:** [Executive FAQ](executive-faq.md) · [Risks and Limitations](risks-and-limitations.md)
