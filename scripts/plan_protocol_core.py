#!/usr/bin/env python3
"""Deterministic helpers for the Evidence-Gated Delivery Plan protocol."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PLAN_PROTOCOL_V1 = "plan-protocol/v1"
PLAN_PROTOCOL_V2 = "plan-protocol/v2"
WORKFLOW_VERSION_V2 = "evidence-gated-delivery/plan-protocol-v2"
SUPPORTED_PLAN_PROTOCOLS = frozenset((PLAN_PROTOCOL_V1, PLAN_PROTOCOL_V2))
GRAPH_POLICY_VERSION = "graph-policy/v1"
ZERO_HASH = "0" * 64
SENSITIVE_VALUE_PATTERNS = (
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("bearer token", re.compile(r"(?i)\bBearer[ \t]+[A-Za-z0-9._~+/=-]{16,}")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "assigned secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)"
            r"[ \t]*[:=][ \t]*['\"]?[A-Za-z0-9._~+/=-]{12,}"
        ),
    ),
    (
        "email address",
        re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    ),
)
ALLOWED_EVENT_TYPES = frozenset(
    (
        "protocol_initialized",
        "protocol_migrated",
        "candidate_linted",
        "audit_completed",
        "finding_dispositioned",
        "issue_read_back",
        "graph_policy_evaluated",
        "graph_draft_frozen",
        "graph_authorized",
        "graph_action_recorded",
        "graph_reconciled",
        "checkpoint_issued",
        "phase_validated",
    )
)
FINDING_SEVERITIES = frozenset(("Blocker", "High", "Medium", "Low"))
FINDING_DISPOSITIONS = frozenset(("open", "verified_fixed", "accepted", "deferred"))
AUDIT_KINDS = frozenset(("preliminary", "remediation_recheck", "final_remote"))
TASK_FIELDS = (
    ("Objective", "objective"),
    ("Context", "context"),
    ("Affected modules", "affected_modules"),
    ("Requirements", "requirements"),
    ("Verification", "verification"),
    ("Complete when", "complete_when"),
    ("Owner lane", "owner_lane"),
)
PLAN_REQUIRED_HEADINGS = (
    "Problem Statement",
    "Personas",
    "Value Assessment",
    "User Stories",
    "Design",
    "Tasks",
    "Out of Scope",
    "Acceptance Criteria",
    "Mockup Accounting Matrix",
    "Cross-Reference",
)


class PlanProtocolError(ValueError):
    """Raised when protocol evidence fails a deterministic invariant."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def protocol_activation_receipt_path(run_id: str) -> Path:
    """Return the derived write-once v2 activation receipt path for a run."""

    if not isinstance(run_id, str) or not run_id.strip():
        raise PlanProtocolError("v2 activation requires a non-empty run_id")
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    name = hashlib.sha256(run_id.strip().encode("utf-8")).hexdigest() + ".json"
    return codex_home / "evidence-gated-delivery" / "protocol-activations" / name


def record_protocol_activation(
    manifest: dict[str, Any], event: dict[str, Any]
) -> dict[str, Any]:
    """Persist immutable activation evidence outside the mutable manifest/event chain."""

    run_id = manifest.get("run_id")
    path = protocol_activation_receipt_path(run_id)
    payload = {
        "run_id": run_id.strip(),
        "parent_thread_id": manifest.get("parent_thread_id"),
        "plan_protocol_version": PLAN_PROTOCOL_V2,
        "workflow_version": WORKFLOW_VERSION_V2,
        "repo_root": manifest.get("repo_root"),
        "starting_commit": manifest.get("starting_commit"),
        "run_started_at": manifest.get("run_started_at"),
        "activated_at": event.get("recorded_at"),
        "activation_event_id": event.get("event_id"),
        "activation_event_sha256": event.get("event_sha256"),
    }
    receipt = {**payload, "receipt_sha256": sha256_json(payload)}
    rendered = json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PlanProtocolError("v2 activation receipt is unreadable") from exc
        if existing != receipt:
            raise PlanProtocolError("v2 activation receipt already exists with different evidence")
        return receipt
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    return receipt


def validate_protocol_activation_receipt(
    manifest: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Read and authenticate any external v2 activation receipt for this run."""

    run_id = manifest.get("run_id")
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    root = codex_home / "evidence-gated-delivery" / "protocol-activations"
    paths: list[Path] = []
    if isinstance(run_id, str) and run_id.strip():
        direct = protocol_activation_receipt_path(run_id)
        if direct.exists():
            paths.append(direct)
    if not paths and root.is_dir():
        parent_thread_id = manifest.get("parent_thread_id")
        if isinstance(parent_thread_id, str) and parent_thread_id.strip():
            for candidate in sorted(root.glob("*.json")):
                try:
                    value = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                if (
                    isinstance(value, dict)
                    and value.get("parent_thread_id") == parent_thread_id
                ):
                    paths.append(candidate)
    if not paths:
        return False, []
    if len(paths) != 1:
        return True, ["external v2 activation registry has ambiguous baseline matches"]
    path = paths[0]
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return True, ["external v2 activation receipt is unreadable"]
    if not isinstance(receipt, dict):
        return True, ["external v2 activation receipt must be an object"]
    claimed = receipt.get("receipt_sha256")
    payload = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    errors: list[str] = []
    if claimed != sha256_json(payload):
        errors.append("external v2 activation receipt hash mismatch")
    bindings = (
        "run_id",
        "parent_thread_id",
        "repo_root",
        "starting_commit",
        "run_started_at",
        "workflow_version",
        "plan_protocol_version",
    )
    for field in bindings:
        expected = manifest.get(field)
        if field == "run_id" and isinstance(expected, str):
            expected = expected.strip()
        if receipt.get(field) != expected:
            errors.append(f"external v2 activation receipt {field} mismatch")
    if receipt.get("plan_protocol_version") != PLAN_PROTOCOL_V2:
        errors.append("external activation receipt must bind plan-protocol/v2")
    if receipt.get("workflow_version") != WORKFLOW_VERSION_V2:
        errors.append("external activation receipt must bind the v2 workflow")
    events = manifest.get("plan_events")
    matching_event = next(
        (
            event
            for event in events or []
            if isinstance(event, dict)
            and event.get("event_id") == receipt.get("activation_event_id")
        ),
        None,
    )
    if matching_event is None:
        errors.append("external v2 activation event is missing from the manifest chain")
    else:
        if matching_event.get("event_sha256") != receipt.get("activation_event_sha256"):
            errors.append("external v2 activation event hash mismatch")
        if matching_event.get("recorded_at") != receipt.get("activated_at"):
            errors.append("external v2 activation timestamp mismatch")
    return True, errors


def privacy_violations(value: Any, path: str = "$") -> list[str]:
    """Return paths containing concrete credential or direct-contact material."""

    violations: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            violations.extend(privacy_violations(str(key), f"{path}.<key>"))
            violations.extend(privacy_violations(nested, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            violations.extend(privacy_violations(nested, f"{path}[{index}]"))
    elif isinstance(value, str):
        for label, pattern in SENSITIVE_VALUE_PATTERNS:
            if pattern.search(value):
                violations.append(f"{path} contains a {label}")
    return violations


def canonicalize_issue_body(body: str) -> str:
    if not isinstance(body, str):
        raise PlanProtocolError("issue body must be a string")
    return body.replace("\r\n", "\n").replace("\r", "\n")


def issue_body_sha256(body: str) -> str:
    return hashlib.sha256(canonicalize_issue_body(body).encode("utf-8")).hexdigest()


def effective_protocol_version(manifest: dict[str, Any]) -> str:
    version = manifest.get("plan_protocol_version")
    if version is None:
        raise PlanProtocolError(
            "plan_protocol_version is required; legacy runs must explicitly retain "
            "plan-protocol/v1 before resume or migration"
        )
    if version not in SUPPORTED_PLAN_PROTOCOLS:
        raise PlanProtocolError(f"unsupported Plan protocol version: {version!r}")
    return version


def validate_protocol_version(
    manifest: dict[str, Any], *, expected: str | None = None
) -> list[str]:
    errors: list[str] = []
    try:
        version = effective_protocol_version(manifest)
    except PlanProtocolError as exc:
        return [str(exc)]
    if expected is not None and version != expected:
        errors.append(
            f"Plan protocol version drift: expected {expected!r}, observed {version!r}"
        )
    return errors
