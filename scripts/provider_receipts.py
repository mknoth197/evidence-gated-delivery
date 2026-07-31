#!/usr/bin/env python3
"""Provider-neutral, fail-closed delegated-audit receipt verification.

This module deliberately does not try to interpret another runtime's private
transcript format.  A provider integration writes one normalized JSON receipt;
the validator verifies the receipt's immutable bindings and refuses it unless
the provider declares the transcript location as readable and integrity-bound.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {
    "provider",
    "provider_version",
    "parent_session_id",
    "child_session_id",
    "delegated_role",
    "started_at",
    "completed_at",
    "transcript_path",
    "transcript_sha256",
    "final_result",
    "final_result_sha256",
    "parent_child_binding_sha256",
}


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value.lower()
    )


def _within(path: Path, roots: list[Any]) -> bool:
    resolved = path.resolve()
    for root in roots:
        if not isinstance(root, str) or not root:
            continue
        try:
            resolved.relative_to(Path(root).expanduser().resolve())
            return True
        except ValueError:
            continue
    return False


def provider_delegated_audit_evidence(
    data: dict[str, Any], audit: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """Read one adapter receipt and prove its parent/child/result bindings.

    The supported contract is intentionally strict: a missing transcript,
    allow-list, digest, provider version, or binding is a failure, never a
    fallback to a self-authored manifest field.
    """
    context = data.get("provider_context")
    path_value = audit.get("provider_receipt_path")
    if not isinstance(context, dict):
        return None, "provider receipt needs provider_context"
    if not isinstance(path_value, str) or not path_value:
        return None, "provider receipt needs provider_receipt_path"
    receipt_path = Path(path_value).expanduser()
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"provider receipt is unreadable: {exc}"
    if not isinstance(receipt, dict) or REQUIRED_FIELDS - receipt.keys():
        return None, "provider receipt lacks required normalized fields"
    if receipt["provider"] != context.get("provider"):
        return None, "provider receipt provider mismatch"
    if receipt["provider_version"] != context.get("provider_version"):
        return None, "provider receipt provider version mismatch"
    if receipt["parent_session_id"] != data.get("parent_thread_id"):
        return None, "provider receipt parent session mismatch"
    if receipt["child_session_id"] != audit.get("agent_id"):
        return None, "provider receipt child session mismatch"
    if receipt["delegated_role"] != audit.get("role_marker"):
        return None, "provider receipt delegated role mismatch"
    started, completed = _timestamp(receipt["started_at"]), _timestamp(receipt["completed_at"])
    if started is None or completed is None or completed < started:
        return None, "provider receipt timestamps are invalid"
    transcript = Path(receipt["transcript_path"]).expanduser()
    roots = context.get("allowed_transcript_roots")
    if not isinstance(roots, list) or not _within(transcript, roots):
        return None, "provider transcript path is not allowlisted"
    try:
        transcript_bytes = transcript.read_bytes()
    except OSError as exc:
        return None, f"provider transcript is unreadable: {exc}"
    if not _sha256(receipt["transcript_sha256"]) or hashlib.sha256(transcript_bytes).hexdigest() != receipt["transcript_sha256"]:
        return None, "provider transcript digest mismatch"
    final_result = receipt["final_result"]
    if not isinstance(final_result, str) or final_result != audit.get("result"):
        return None, "provider receipt final result mismatch"
    if not _sha256(receipt["final_result_sha256"]) or hashlib.sha256(final_result.encode()).hexdigest() != receipt["final_result_sha256"]:
        return None, "provider receipt final-result digest mismatch"
    binding = "\n".join(
        str(receipt[key])
        for key in ("provider", "provider_version", "parent_session_id", "child_session_id", "delegated_role", "started_at", "completed_at", "transcript_sha256", "final_result_sha256")
    )
    if not _sha256(receipt["parent_child_binding_sha256"]) or hashlib.sha256(binding.encode()).hexdigest() != receipt["parent_child_binding_sha256"]:
        return None, "provider parent-child binding digest mismatch"
    return {"final_message": final_result, "completed_at": receipt["completed_at"], "delegation_started_at": receipt["started_at"], "session_meta": {"provider": receipt["provider"]}}, None
