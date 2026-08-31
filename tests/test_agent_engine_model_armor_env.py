"""Regression test for issue #203: MODEL_ARMOR_TEMPLATE was set by Terraform
only when the Agent Gateway was OFF (`var.enable_agent_gateway ? {} : {...}`),
and the gateway defaults on -- so `agent/main.py`'s SREAgent._sanitize() (and,
downstream, agent/mcp_client.py's custom-MCP response sanitize from PR #223)
was dead code in every real deployment.

Root cause confirmed by direct reads (not assumed):
  - iac/agent/agent_gateway.tf's own header: the gateway has no working
    CONTENT_AUTHZ path ("no working Terraform path exists to wire CONTENT_AUTHZ
    to this gateway"), so the original gating comment's premise (a gateway-side
    Model Armor extension making the app-level call redundant) never held.
  - iac/agent/model_armor.tf's floor setting (google_model_armor_floorsetting.mcp)
    only covers integrated_services = ["GOOGLE_MCP_SERVER", "AI_PLATFORM"], never
    the agent's own pre-assembly query text or its downstream-constructed RCA
    summary -- confirmed in issue #203's own investigation.

Fix: MODEL_ARMOR_TEMPLATE is now set unconditionally. This test parses the
actual Terraform source (not a mock) so a future re-introduction of the
gateway-conditional gate on this specific env var fails CI, rather than
silently reintroducing the dead-code path. It intentionally does NOT require
a full HCL parser/terraform binary -- a targeted text assertion is enough to
pin this specific regression and matches this repo's existing lightweight
config-assertion style.
"""

import re
from pathlib import Path

AGENT_ENGINE_TF = (
    Path(__file__).resolve().parent.parent / "iac" / "agent" / "agent_engine.tf"
).read_text()

MODEL_ARMOR_TF = (
    Path(__file__).resolve().parent.parent / "iac" / "agent" / "model_armor.tf"
).read_text()


def test_model_armor_template_is_not_gated_on_enable_agent_gateway():
    """The specific regression this issue was: MODEL_ARMOR_TEMPLATE assigned
    inside `var.enable_agent_gateway ? {} : { MODEL_ARMOR_TEMPLATE = ... }`.
    That exact shape must never come back."""
    stale_gate = re.search(
        r"var\.enable_agent_gateway\s*\?\s*\{\}\s*:\s*\{[^}]*MODEL_ARMOR_TEMPLATE",
        AGENT_ENGINE_TF,
        re.DOTALL,
    )
    assert stale_gate is None, (
        "MODEL_ARMOR_TEMPLATE is gated behind `var.enable_agent_gateway ? {} : "
        "{...}` again -- this reintroduces issue #203 (app-level Model Armor "
        "silently dead in every real deployment, since the gateway defaults on)."
    )


def test_model_armor_template_assignment_present_unconditionally():
    """Positive assertion: the assignment exists, and is not wrapped in ANY
    ternary/ for_each conditional on enable_agent_gateway within the same
    locals block that builds local.agent_env."""
    assert (
        "MODEL_ARMOR_TEMPLATE = google_model_armor_template.sre_agent_request.name"
        in AGENT_ENGINE_TF
    )

    # Extract the `locals { agent_env = merge(...) }` block and confirm the
    # MODEL_ARMOR_TEMPLATE line is not inside a `var.enable_agent_gateway ? ... : ...`
    # sub-expression anywhere in that merge() call.
    merge_start = AGENT_ENGINE_TF.index("locals {")
    merge_block = AGENT_ENGINE_TF[merge_start:]
    armor_line_idx = merge_block.index("MODEL_ARMOR_TEMPLATE =")
    # Look at the nearest enclosing map literal only (from the last unmatched
    # "{" before the assignment to the matching "}") -- crude but sufficient:
    # walk backwards to the start of that specific map entry's opening brace.
    preceding = merge_block[:armor_line_idx]
    last_open_brace = preceding.rfind("{")
    map_open_context = preceding[max(0, last_open_brace - 80) : last_open_brace]
    assert "enable_agent_gateway" not in map_open_context, (
        "MODEL_ARMOR_TEMPLATE's containing map literal is opened by an "
        "enable_agent_gateway-conditional expression -- the gate is back."
    )


def test_gateway_content_authz_claim_corrected_in_both_files():
    """Both files previously asserted, as live fact, that the Agent Gateway
    inspects content via a Model Armor CONTENT_AUTHZ extension. agent_gateway.tf's
    own header says no such extension exists (only the IAP REQUEST_AUTHZ one).
    Both files must now cite that correction -- not just quote the old theory for
    context, but actually say it was confirmed false, so a future reader doesn't
    re-derive the same wrong conclusion (this is the exact failure mode issue
    #203 called out: "the next person will re-derive the same wrong conclusion")."""
    for label, text in (("agent_engine.tf", AGENT_ENGINE_TF), ("model_armor.tf", MODEL_ARMOR_TF)):
        normalized = " ".join(text.replace("#", " ").split())
        assert "no working Terraform path exists to wire CONTENT_AUTHZ" in normalized, (
            f"{label} does not cite agent_gateway.tf's own confirmation that no "
            "CONTENT_AUTHZ extension exists on the gateway -- the stale claim may "
            "not have been corrected."
        )


def test_gke_remote_double_inspection_guard_still_documented_and_present():
    """The one real double-inspection risk (GKE Remote MCP responses, which the
    floor setting's GOOGLE_MCP_SERVER integration already covers) must still be
    excluded -- in code, not infra. This does not change with this fix and must
    not regress."""
    mcp_client = (Path(__file__).resolve().parent.parent / "agent" / "mcp_client.py").read_text()
    assert "if not is_gke_remote:" in mcp_client
    assert "_sanitize_custom_mcp_response" in mcp_client
