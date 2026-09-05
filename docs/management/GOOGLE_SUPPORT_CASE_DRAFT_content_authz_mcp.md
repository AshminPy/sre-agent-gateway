# Google Cloud support case — draft (not yet filed)

**Status:** prepared 2026-09-05, non-blocking follow-up to Phase 1. Not yet submitted — filing requires going through the Google Cloud Console support flow with an authenticated, entitled account, which this session cannot do on the user's behalf. Phase 1 completion does not wait for a response.

## Suggested case title
Agent Gateway CONTENT_AUTHZ / Model Armor: MCP `tools/call` RESPONSE_BODY never inspected for Streamable HTTP transport

## Environment
- Project: `sreagent-t2-demo` (project number `327234009108`)
- Agent Gateway: `projects/sreagent-t2-demo/locations/us-central1/agentGateways/sre-agent-egress` (AGENT_TO_ANYWHERE, google-managed)
- Provider: `google-beta` 7.43.0, Terraform 1.4.7
- CONTENT_AUTHZ extension: `sre-agent-model-armor-authz` (service `modelarmor.us-central1.rep.googleapis.com`)
- Model Armor templates: `sre-agent-request-guard`, `sre-agent-response-guard` (both `enforcement_type=INSPECT_AND_BLOCK`, `log_sanitize_operations=true`)
- Two MCP endpoints tested: our own custom Cloud Run MCP server (`sre-k8s-mcp`, FastMCP, `transport="streamable-http"`) and Google's own first-party GKE Remote MCP (`container.googleapis.com`)

## What we configured
`google_network_services_authz_extension` (CONTENT_AUTHZ profile) + `google_network_security_authz_policy` attached to the Agent Gateway, referencing both Model Armor templates. IAM verified correct: `roles/modelarmor.calloutUser`, `roles/serviceusage.serviceUsageConsumer`, `roles/modelarmor.user` all granted to the gateway's own Service Extensions service agent (confirmed via the gateway resource's own `agentGatewayCard.serviceExtensionsServiceAccount` field, not assumed).

## What we observed
For a real MCP `tools/call` round trip through the gateway on either MCP path:
- `serviceExtensionInfo.perProcessingRequestInfo` shows `REQUEST_HEADERS` (processingEffect: NONE), `REQUEST_BODY` (processingEffect: CONTENT_MODIFIED), `RESPONSE_HEADERS` (processingEffect: NONE).
- **`RESPONSE_BODY` never appears as a processed event at all**, on either MCP path.
- Even the `REQUEST_BODY` event that does fire, showing `CONTENT_MODIFIED`, never produces a corresponding entry in `modelarmor.googleapis.com%2Fsanitize_operations` under either `sre-agent-request-guard` or `sre-agent-response-guard` — every detection in that log stream during our tests came from a separate, pre-existing, always-on floor-setting mechanism (`template_id: FLOOR_SETTING-19050`), not from the new CONTENT_AUTHZ path.
- Two independent malicious test payloads (a prompt-injection string, and Google's own documented guaranteed-detection Safe Browsing test URL `testsafebrowsing.appspot.com/s/malware.html`) both passed through completely unblocked and undetected under our app-specific templates, on both MCP paths.

## Documentation we found that appears to explain this
Model Armor's own Agent Gateway integration documentation (docs.cloud.google.com/model-armor/model-armor-agent-gateway-integration) states, under MCP payloads:
> Sanitized: `tools/call` request and response, `prompts/get` request and response, MCP tool execution errors.
> Allowed without sanitization: `tools/list`, `resources/*`, `notifications/*`, **Streamable HTTP/SSE for MCP**, MCP protocol errors.

Our custom MCP server's own code confirms it uses `transport="streamable-http"` — exactly the excluded category. We also observed the identical failure signature on Google's own GKE Remote MCP path, though we cannot confirm its internal transport from documentation.

## What we're asking Google
1. **Confirm** whether the "Streamable HTTP/SSE for MCP" exclusion is the correct, complete explanation for the behavior above — specifically, does it mean RESPONSE_BODY inspection is categorically unsupported for any MCP server using this transport, including via Agent Gateway's CONTENT_AUTHZ, or is something else (e.g., our specific configuration) also contributing?
2. **Roadmap**: is response-body inspection for Streamable HTTP/SSE MCP transports planned, and if so on what timeline?
3. **Recommended interim control**: given Streamable HTTP is the MCP specification's own recommended transport, what does Google recommend as the supported blocking control for MCP tool-response content in the meantime (e.g., is the floor-setting mechanism's inspect-only detection the intended interim mitigation, or is there a different recommended pattern)?

## Evidence available on request
Full command-by-command evidence trail, real run IDs, and exact gateway/Model Armor log excerpts: `PHASE1_EVIDENCE_LOG.md` on branch `feat/phase-1-release`, repo `AshminPy/sre-agent-gateway`.
