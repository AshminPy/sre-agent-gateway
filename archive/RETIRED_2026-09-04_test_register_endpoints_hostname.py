"""Regression test for issue #30: the Agent Registry entry for Model Armor was missing
the required ".rep." segment in its regional endpoint URL (registered as
modelarmor.us-central1.googleapis.com; the correct, documented, code-matching format is
modelarmor.us-central1.rep.googleapis.com). Root cause: the generic regional_only
pattern (base.{region}.googleapis.com) doesn't know about Model Armor's special shape.

This test proves two things together, since fixing only the first while breaking the
second would have silently orphaned the live registry entry (a real risk identified
during the fix's own design, not a hypothetical): the corrected interface URL is
produced, AND the derived resource ID is IDENTICAL to what was already live
(us-central1-modelarmor-us-central1) -- so a re-run of this script targets the same
resource via `services update`-style reconciliation rather than creating a duplicate.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from register_endpoints import (  # noqa: E402
    derive_resource_name,
    _REGIONAL_INTERFACE_HOSTNAME_OVERRIDES,
)


def test_modelarmor_interface_hostname_includes_rep():
    template = _REGIONAL_INTERFACE_HOSTNAME_OVERRIDES["modelarmor.googleapis.com"]
    assert template.format(region="us-central1") == "modelarmor.us-central1.rep.googleapis.com"


def test_modelarmor_resource_id_unchanged_by_the_rep_fix():
    """The #30 fix must not change WHICH resource is targeted, only the URL it points
    at -- resource-name derivation always uses the un-.rep.'d base hostname."""
    identity_host = "modelarmor.us-central1.googleapis.com"
    assert derive_resource_name(identity_host, "us-central1", "us-central1") == "us-central1-modelarmor-us-central1"


def test_other_regional_only_hosts_have_no_override():
    """The override is scoped to Model Armor only -- it must not affect any other
    regional_only host's interface URL."""
    assert "cloudtrace.googleapis.com" not in _REGIONAL_INTERFACE_HOSTNAME_OVERRIDES
    assert "logging.googleapis.com" not in _REGIONAL_INTERFACE_HOSTNAME_OVERRIDES


def test_mtls_variant_not_covered_by_the_override():
    """Explicitly out of scope for #30 -- no verified evidence exists yet for the
    correct mTLS+regional Model Armor hostname shape."""
    assert "modelarmor.mtls.googleapis.com" not in _REGIONAL_INTERFACE_HOSTNAME_OVERRIDES


if __name__ == "__main__":
    test_modelarmor_interface_hostname_includes_rep()
    test_modelarmor_resource_id_unchanged_by_the_rep_fix()
    test_other_regional_only_hosts_have_no_override()
    test_mtls_variant_not_covered_by_the_override()
    print("ALL 4 PASS")
