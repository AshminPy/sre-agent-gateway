# SRE Agent Gateway — Speaking Script

Maps 1:1 to `presentation.md`. Say the plain-English sentence; the bracketed note is
for you, not to be read aloud. Where a slide has a live-demo counterpart, the exact
commands are in `demo-runbook.md` under the matching numbered section — run them from
there, not from memory.

---

## Chapter 1 — What did we build?

**Slide 1 (Title)**
"Today I'm going to show you the SRE Agent Gateway — an AI system that investigates
Kubernetes incidents for us, live, on real clusters."

**Slide 2 (Pitch)**
"In one sentence: you give it a broken pod or a failing cluster, and it comes back
with a root cause analysis — backed by real evidence, not a guess. It's read-only by
design — it investigates, it never changes anything in your cluster."

**Slide 3 (Architecture)**
"The request comes into Vertex AI Agent Engine, which runs a multi-step reasoning
workflow. Every outbound call goes through one gateway that enforces authorization and
content safety. From there it reaches either a GKE cluster directly, through Google's
own managed connector, or a non-GKE cluster — think on-prem, or another cloud — through
GCP Fleet and Connect Gateway. Evidence it collects is stored, and the final RCA is
built from that evidence, not invented."

**Slide 4 (Phase 1 scope)**
"Phase 1 was scoped to nine concrete, testable outcomes — not a vague MVP. [Read the
bullets you're most proud of; don't read all nine verbatim, pick 3-4.]"

**Slide 5 (Security boundaries)**
"Three things I want you to trust before I show you anything live: it cannot write to
your cluster — that's enforced four separate ways, not just a policy on paper. It never
holds a long-lived credential — every identity here is short-lived and workload-federated.
And there's a content-safety layer in front of every model call. [If asked about gaps:]
There's one known platform limitation — Google's MCP transport doesn't let us content-scan
tool responses coming back from GKE's own managed connector. We know about it, it's documented,
we're not hiding it."

---

## Chapter 2 — Does it actually work?

**Slide 6 (GKE live incident) — DEMO, runbook section 2**
"Let me show you this for real. I'm going to run a broken pod on our GKE cluster right
now." [Run the fixture pod + invoke the agent per runbook §2.] "Here's the RCA it just
produced."

**Slide 7 (Verify GKE RCA) — DEMO, runbook section 3**
"I'm not going to ask you to take that on faith." [Run the independent kubectl
verification per runbook §3.] "Same root cause, straight from Kubernetes itself."

**Slide 8 (On-prem live incident) — DEMO, runbook section 4**
"Now the harder case — a cluster that isn't GKE at all. This one's reached through
GCP Fleet and Connect Gateway instead." [Run per runbook §4.] "Same agent, same
workflow, different cluster type — no special-cased code path."

**Slide 9 (Verify on-prem RCA) — DEMO, runbook section 5**
"Same independent check, through the same gateway path the agent itself uses." [Run
per runbook §5.]

**Slide 10 (Unauthorized cluster refusal) — DEMO, runbook section 6**
"One more thing that matters more than the successes: what happens when it's asked
about a cluster it doesn't know about?" [Run per runbook §6.] "It doesn't guess. It
stops, and tells us exactly why — that refusal is enforced twice, independently, in
the code."

---

## Chapter 3 — How do we operate it?

**Slide 11 (Add a GKE cluster)**
"Adding a new GKE cluster to this system is a config change, not a code change — one
block in Terraform, one Kubernetes permission, apply." [Optionally show the config
block per runbook §7, don't need to run it live.]

**Slide 12 (Add an on-prem cluster, Ansible) — DEMO, runbook section 8**
"On-prem used to mean a page of manual gcloud and kubectl commands, run by hand, easy
to get wrong. Now it's one command." [Run per runbook §8.] "That one playbook run
registers it with GCP, applies the minimum permissions, and proves the permissions
work — all in one pass."

**Slide 13 (Least-privilege proof) — DEMO, runbook section 9**
"Let's prove 'least privilege' isn't just a phrase." [Run per runbook §9 — show IAM
roles, then show a live denied command.] "It can read. It cannot delete, create, or
touch a secret — and I just showed you that live, not told you."

**Slide 14 (Observability) — DEMO, runbook section 10**
"Every investigation is fully logged and traceable by a run ID." [Show logs command
and dashboard per runbook §10.]

---

## Chapter 4 — How do we extend and release it?

**Slide 15 (Switch the LLM)**
"If we need to change the underlying model — say, a newer Gemini version — that's a
configuration value, not a rewrite of the agent's reasoning. [Say the honest part
out loud:] There is one real gap we found this week preparing for today: our CI
pipeline currently hardcodes the model choice too, so today a full switch through the
normal pipeline needs one more file updated, not just the config. We're not hiding
that — it's a fast fix, not a redesign. Switching to a completely different model
vendor is real future work, not something we support today."

**Slide 16 (Add a new MCP)**
"The way we'd add a new tool source — say, Prometheus — is designed to be config-driven,
the same pattern as adding a cluster. I want to be precise: we haven't built a second
one yet. This is the documented extension pattern, not a live second integration."

**Slide 17 (CI/CD, infrastructure)**
"Every infrastructure change goes through a pull request, an automated plan that's
posted for review, and a manual approval gate before it's applied — nothing reaches
production on merge alone."

**Slide 18 (MCP image CI/CD)**
"The MCP service itself has its own build-and-deploy stage in that same pipeline —
it only fires when the MCP code actually changes, and it health-checks the new
version before switching traffic to it."

**Slide 19 (Documentation)**
"Everything I've shown you today is written down — onboarding runbooks, architecture
docs, security docs. [Be honest:] Some of it is a few weeks old and we know it needs a
refresh pass — that's tracked, not ignored."

---

## Chapter 5 — What comes next?

**Slide 20 (Known Phase 1 limitations)**
"I'd rather tell you what's not done than let you find out later. [Pick the 3-4 most
relevant to this audience from the slide.] And I'll be direct about one real incident:
this week, while preparing for today, we found that on-prem cluster registration was
quietly costing real money — about two hundred dollars a month — because of how GCP
bills third-party clusters. We caught it, fixed the workflow so it now requires
explicit cost approval before that can happen again, and I think that's exactly the
kind of operational discipline this project is building in, not something to be
embarrassed about."

**Slide 21 (Phase 2 direction)**
"Here's where we'd take this next, assuming today goes well: [read the bullets you
actually intend to pursue — don't read the full backlog, pick the real priorities]."

**Slide 22 (Close / ask)**
"[State your actual ask plainly — budget, headcount, approval to proceed. Stop talking
after you say it; let the room respond.]"
