"""Regression test for the register_endpoints.py landmine found during the
observability review (docs/management/observability-architecture-review-2026-08-23.md
section 1, mistakes.yaml M018): this file used to list logging.googleapis.com /
logging.mtls.googleapis.com as GRPC-only, which would silently re-register them
as GRPC on any full re-registration (fresh project, DR, manual delete+recreate)
and reintroduce issue #139's 403.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from register_endpoints import protocol_binding_for  # noqa: E402


def test_cloud_logging_hosts_register_as_http_json_not_grpc():
    """The #139 fix: Cloud Logging writes work over HTTP_JSON, not GRPC."""
    assert protocol_binding_for("logging.googleapis.com") == "HTTP_JSON"
    assert protocol_binding_for("logging.mtls.googleapis.com") == "HTTP_JSON"


def test_cloud_trace_hosts_still_register_as_grpc():
    """Unrelated to #139 -- Cloud Trace's low-level client really is GRPC-only,
    this must not regress while fixing the logging hosts above."""
    assert protocol_binding_for("cloudtrace.googleapis.com") == "GRPC"
    assert protocol_binding_for("cloudtrace.mtls.googleapis.com") == "GRPC"


def test_unlisted_hosts_default_to_jsonrpc():
    assert protocol_binding_for("container.googleapis.com") == "JSONRPC"
