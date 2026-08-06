"""
GCS evidence store.
Writes sanitized raw MCP responses by evidence_id.
Raw MCP output never enters LLM context — only compressed facts do.
"""
import json
import logging
import os
import time

from google.cloud import storage

log = logging.getLogger("sre-agent.gcs")

BUCKET = os.environ.get("EVIDENCE_BUCKET", "your-gcp-project-id-evidence")

_client: storage.Client | None = None


def _get_client() -> storage.Client:
    global _client
    if _client is None:
        _client = storage.Client()
    return _client


def write_evidence(
    run_id: str,
    evidence_id: str,
    sanitized_data: dict,
) -> str:
    """
    Write sanitized MCP response to GCS.
    Returns gs:// path on success, "gcs_write_failed:{path}" on failure.
    Retries once before giving up — callers must check for gcs_write_failed prefix.
    """
    path = f"{run_id}/{evidence_id}.json"
    for attempt in range(2):
        try:
            client = _get_client()
            bucket = client.bucket(BUCKET)
            blob = bucket.blob(path)
            blob.upload_from_string(
                json.dumps(sanitized_data, indent=2),
                content_type="application/json",
            )
            raw_ref = f"gs://{BUCKET}/{path}"
            log.info("Evidence written: %s", raw_ref)
            return raw_ref
        except Exception as e:
            if attempt == 0:
                log.warning("GCS write attempt 1 failed for %s: %s — retrying", evidence_id, e)
                time.sleep(1)
            else:
                log.error(
                    "GCS write failed for %s after 2 attempts: %s — audit chain broken",
                    evidence_id, e,
                )
                _log_evidence_storage_failure(run_id, evidence_id, path, str(e))
                return f"gcs_write_failed:{path}"
    return f"gcs_write_failed:{path}"


def _log_evidence_storage_failure(run_id: str, evidence_id: str, path: str, error: str) -> None:
    """Structured Cloud Logging event for a permanent evidence-storage failure (both write
    attempts exhausted). Same pattern as tool_executor.py's sre-agent-tool-failures log —
    a dedicated logName lets iac/agent/monitoring.tf build a log-based metric + alert
    without depending on a generic textPayload grep. See PRODUCTION-LAUNCH-PLAN.md
    Priority 10 ("evidence-storage success/failure").
    """
    try:
        from google.cloud import logging as cloud_logging

        cloud_logging.Client().logger("sre-agent-evidence-storage-failures").log_struct(
            {
                "event":       "evidence_storage_failure",
                "run_id":      run_id,
                "evidence_id": evidence_id,
                "path":        path,
                "bucket":      BUCKET,
                "error":       error[:300],
            },
            severity="ERROR",
        )
    except Exception:
        # Never let observability logging break the investigation.
        pass


def read_evidence(raw_ref: str) -> dict:
    """
    Re-read raw evidence from GCS by raw_ref (gs:// path).
    Used for on-demand detail retrieval without holding raw data in state.
    """
    if not raw_ref or not raw_ref.startswith("gs://"):
        log.warning("read_evidence: invalid raw_ref '%s'", raw_ref)
        return {}
    try:
        path = raw_ref[5:]  # strip "gs://"
        bucket_name, blob_path = path.split("/", 1)
        client = _get_client()
        blob = client.bucket(bucket_name).blob(blob_path)
        return json.loads(blob.download_as_text())
    except Exception as e:
        log.error("read_evidence failed for %s: %s", raw_ref, e)
        return {}


def redact(raw: dict) -> dict:
    """
    PII redaction before GCS write and before LLM context.
    Redacts: email addresses, IP addresses, tokens, passwords.
    Uses default=str to handle non-JSON-serializable values without data loss.
    Called at evidence_extractor — before any other processing.
    """
    import copy
    import re

    data = copy.deepcopy(raw)
    # default=str converts datetimes, sets, etc. — prevents TypeError data loss
    data_str = json.dumps(data, default=str)

    # Redact email addresses
    data_str = re.sub(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "[REDACTED_EMAIL]",
        data_str,
    )
    # Redact IPv4 addresses
    data_str = re.sub(
        r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
        "[REDACTED_IP]",
        data_str,
    )
    # Redact bearer tokens
    data_str = re.sub(
        r"Bearer\s+[A-Za-z0-9\-._~+/]+=*",
        "Bearer [REDACTED_TOKEN]",
        data_str,
    )
    # Redact password-like fields
    data_str = re.sub(
        r'"(password|token|secret|key|credential)"\s*:\s*"[^"]*"',
        r'"\1": "[REDACTED]"',
        data_str,
        flags=re.IGNORECASE,
    )
    try:
        return json.loads(data_str)
    except Exception:
        log.error("redact: JSON parse failed after substitution — returning safe fallback")
        return {"redacted": True, "error": "redaction_parse_failed"}
