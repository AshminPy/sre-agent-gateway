# Extending Model Armor coverage to custom / future MCP servers

**Date:** 2026-08-25. Updated same day with a real live test — not just code reading. Answers: what does it actually take to protect the custom Cloud Run MCP fallback (and any MCP server added later) the same way `GOOGLE_MCP_SERVER`/`AI_PLATFORM` floor settings now protect the Google-managed paths.

## The short answer, corrected after a real test

**Floor settings genuinely can't cover custom MCP — confirmed empirically, not just from docs.** But an earlier version of this note also claimed custom MCP traffic never reaches the Agent Gateway at all. That was wrong, and a real test the same night proved it wrong — see "Real test performed" below. The gateway does see this traffic. Floor settings still don't inspect it, for an unrelated reason (a closed, Google-only integration list). The fix is the same either way: the app-level Model Armor client, wired into the tool-call function itself.

## Real test performed — not assumed from code alone

Temporarily broke `gke_remote_mcp`'s URL (real host, invalid path — fails fast and clean) to force every investigation onto the custom-MCP fallback, deployed via the normal CI pipeline, then reverted the same way. Real results:

1. **The fallback genuinely works.** CI's own smoke test (not staged): `primary_mcp_source: "gke_remote_mcp"` (tried first, as designed), `actual_mcp_sources: ["k8s_mcp"]` (fell through to custom MCP), smoke test passed.
2. **That traffic DOES transit the Agent Gateway — corrects the earlier claim in this doc.** The gateway's own request log shows a real entry for the custom MCP call: TLS-intercepted (`requestWasTlsIntercepted: true`), IAP-governed (`sre-agent-iap-gateway-policy: ALLOWED`), and the gateway even parsed the MCP protocol details correctly (`mcpInfo: {method: "tools/call", parameter: "describe_pod_detail"}`). The earlier conclusion — "never touches the gateway" — was based on custom MCP never having actually been exercised yet, not on a real structural bypass.
3. **Model Armor still didn't see it — confirmed, not assumed.** In the exact same time window, Model Armor's own log shows 12 real entries, every one `client_name=VERTEX_AI`, **zero** `client_name=GOOGLE_MCP_SERVER`. The gateway saw and governed the traffic; Model Armor's floor-setting integration did not.

## Why floor settings still can't reach this traffic, even though the gateway does

Checked Model Armor's own API schema directly (not assumed): the `integratedServices` field only accepts three values — `INTEGRATED_SERVICE_UNSPECIFIED`, `AI_PLATFORM`, `GOOGLE_MCP_SERVER`. Google's own description for the second: *"Google MCP Server (via Shim Service Extension)"* — a purpose-built integration with Google's own MCP server implementation specifically, not a generic "inspect anything passing through" switch. There's no configuration option to add a custom MCP server to this list — the real test above confirms this isn't a config gap, it's a closed product boundary.

**This also means the Agent Gateway `CONTENT_AUTHZ` approach tried and rejected earlier (PR 1 trial) actually would have reached custom MCP traffic too** — something not understood at the time it was evaluated, since custom MCP had never been exercised through it yet. That mechanism was still rejected on its own merits (never proved response-side inspection), but it wasn't blocked by a custom-MCP-specific limitation the way floor settings are.

## Why floor settings can't reach this traffic — read from the actual code too

Checked `agent/mcp_client.py`'s two call paths directly:

**GKE Remote MCP** (`call_tool()`, `is_gke_remote` branch): gets a token via `_get_access_token()` (Workload Identity / ADC), calls `https://container.googleapis.com/mcp/read-only` — the Google-managed path floor settings cover.

**Custom MCP** (`call_tool()`, the `else` branch, ~line 454): gets a token via `_get_identity_token(url)` — a Cloud Run identity token — and calls the Cloud Run service URL with `httpx.Client().post(url, ...)`. This is not a "Google or Google Cloud remote MCP server" in Model Armor's own terms, regardless of whether the network path happens to transit the gateway.

## What already exists and just isn't wired up

`agent/main.py`'s `SREAgent` class (lines ~847–925) already has a working Model Armor client and a `_sanitize()` method — the same mechanism this whole investigation found to be **dead code** in the current deployment (`MODEL_ARMOR_TEMPLATE` unset when the gateway is on, `iac/agent/agent_engine.tf:106-108`). This is protocol-agnostic by design: it calls Model Armor's `sanitize_user_prompt`/`sanitize_model_response` API directly on a text string, with no dependency on how that text got there. It doesn't care if the content came from Gemini, GKE Remote MCP, or a custom Cloud Run service.

Today it's called in exactly two places: the initial user query (`agent/main.py:1150`) and the final LLM summary (`agent/main.py:1253`). It is **not** called anywhere around an individual MCP tool call.

## Exact steps to make this cover custom MCP (and anything added later)

1. **Fix the env var gating first** (`iac/agent/agent_engine.tf:106-108`) — `MODEL_ARMOR_TEMPLATE` needs to be set regardless of `enable_agent_gateway`, not only when the gateway is off. This is the same gap flagged at the very start of tonight's investigation; nothing here works until this is fixed.
2. **Move the Model Armor client out of `SREAgent`** into a small shared module (e.g. `agent/model_armor_client.py`). `SREAgent` lives in `agent/main.py`; the tool-call site lives in `agent/nodes/tool_executor.py`, which already imports directly from `agent/mcp_client.py` (`from agent.mcp_client import call_tool`) — importing the whole `SREAgent` class into a node file risks a circular import (need to verify the actual import graph before doing this, not assumed here) and isn't the right shape anyway. A small, dependency-free client module avoids that entirely.
3. **Wrap the tool call itself**, at the exact call site: `agent/nodes/tool_executor.py:47`, `result = call_tool(...)`. Sanitize `arguments` before the call (request side) and `result`'s content after it returns (response side) — same two-sided pattern already proven live tonight for the Google-managed paths, just implemented in Python instead of via `google_mcp_server_floor_setting`.
4. **This is what makes it future-proof.** Once step 3 is in place, protection applies to *every* `call_tool()` invocation — GKE Remote MCP, the current custom Cloud Run MCP, and any new MCP source added to `MCP_REGISTRY` later — with zero additional per-server configuration. Adding a new MCP server in the future would need no Model Armor work at all; it's covered automatically by virtue of going through the same `call_tool()` function.
5. **A real decision still needs making, not assumed here:** what happens when `_sanitize()` flags a tool call. The existing behavior for the query/summary path blocks/replaces text; a tool call has different failure modes to consider — does a blocked tool argument fail that one tool call and let the investigation continue (matching this repo's existing fallback philosophy, e.g. `_try_custom_mcp_fallback`), or fail the whole investigation? Flagging this as a real design choice, not deciding it here.
6. **Test the same way as tonight, once built:** a benign investigation through the custom MCP path (confirm no regression), a malicious payload through a tool argument (confirm it's actually caught), and eventually the same SDP check.

## What this does NOT require

No new Terraform resource, no new IAM grant, no new floor-setting-style project-level config per MCP server. The existing `sre_agent_request`/`sre_agent_response` Model Armor templates (`iac/agent/model_armor.tf`) are already exactly what this needs — they're just not being called from the right place in the code yet.

## Not done tonight

The fallback-and-gateway test above was real and live-verified; the app-level sanitization fix (steps above) was not built. Custom MCP remains completely unprotected by Model Armor until those steps are actually implemented and tested — confirmed, not assumed, via the real test in this doc.
