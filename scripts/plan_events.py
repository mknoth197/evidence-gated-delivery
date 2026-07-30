#!/usr/bin/env python3
"""Hash-chained Plan events and protocol migration."""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from plan_protocol_core import (
    ACTIVATION_RECEIPT_V2, ALLOWED_EVENT_TYPES, PLAN_PROTOCOL_V1, PLAN_PROTOCOL_V2,
    WORKFLOW_VERSION_V2, ZERO_HASH, PlanProtocolError, canonical_json,
    activation_receipt_binds_workflow_identity, effective_protocol_version,
    record_protocol_activation, sha256_json,
    protocol_activation_receipt_path, validate_protocol_activation_receipt,
    validate_protocol_version,
)

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_iso8601(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise PlanProtocolError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PlanProtocolError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise PlanProtocolError(f"{field} must include a timezone")


def _event_hash(event: dict[str, Any]) -> str:
    return sha256_json({key: value for key, value in event.items() if key != "event_sha256"})


def validate_plan_events(events: Any) -> list[str]:
    if not isinstance(events, list):
        return ["plan_events must be an array"]
    errors: list[str] = []
    previous = ZERO_HASH
    seen_ids: set[str] = set()
    for index, event in enumerate(events, 1):
        prefix = f"plan_events[{index - 1}]"
        if not isinstance(event, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if event.get("sequence") != index:
            errors.append(f"{prefix}.sequence must equal {index}")
        event_id = event.get("event_id")
        try:
            uuid.UUID(str(event_id))
        except (ValueError, AttributeError):
            errors.append(f"{prefix}.event_id must be a UUID")
        event_id_key = str(event_id)
        if event_id_key in seen_ids:
            errors.append(f"{prefix}.event_id must be unique")
        seen_ids.add(event_id_key)
        if event.get("type") not in ALLOWED_EVENT_TYPES:
            errors.append(f"{prefix}.type is unsupported")
        try:
            _validate_iso8601(event.get("recorded_at"), f"{prefix}.recorded_at")
        except PlanProtocolError as exc:
            errors.append(str(exc))
        if not isinstance(event.get("payload"), dict):
            errors.append(f"{prefix}.payload must be an object")
        if event.get("previous_event_sha256") != previous:
            errors.append(f"{prefix}.previous_event_sha256 does not match chain head")
        try:
            expected_hash = _event_hash(event)
            if event.get("event_sha256") != expected_hash:
                errors.append(f"{prefix}.event_sha256 does not match canonical event")
        except (TypeError, ValueError):
            errors.append(f"{prefix} contains non-JSON event content")
        previous = event.get("event_sha256") if isinstance(event.get("event_sha256"), str) else ""
    return errors


def append_plan_event(
    events: list[dict[str, Any]],
    event_type: str,
    payload: dict[str, Any],
    *,
    recorded_at: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    existing_errors = validate_plan_events(events)
    if existing_errors:
        raise PlanProtocolError("cannot append to invalid event chain: " + "; ".join(existing_errors))
    if event_type not in ALLOWED_EVENT_TYPES:
        raise PlanProtocolError(f"unsupported Plan event type: {event_type!r}")
    if not isinstance(payload, dict):
        raise PlanProtocolError("event payload must be an object")
    timestamp = recorded_at or _utc_now()
    _validate_iso8601(timestamp, "recorded_at")
    identifier = event_id or str(uuid.uuid4())
    try:
        uuid.UUID(identifier)
    except ValueError as exc:
        raise PlanProtocolError("event_id must be a UUID") from exc
    event = {
        "sequence": len(events) + 1,
        "event_id": identifier,
        "type": event_type,
        "recorded_at": timestamp,
        "payload": copy.deepcopy(payload),
        "previous_event_sha256": events[-1]["event_sha256"] if events else ZERO_HASH,
    }
    try:
        event["event_sha256"] = _event_hash(event)
    except (TypeError, ValueError) as exc:
        raise PlanProtocolError("event payload must contain only JSON values") from exc
    events.append(event)
    return event


def migrate_manifest_to_v2(
    manifest: dict[str, Any],
    *,
    recorded_at: str | None = None,
    event_id: str | None = None,
    persist_activation: bool = True,
) -> dict[str, Any]:
    current = effective_protocol_version(manifest)
    if current != PLAN_PROTOCOL_V1:
        raise PlanProtocolError(f"only legacy {PLAN_PROTOCOL_V1} manifests may migrate")
    events = manifest.get("plan_events", [])
    errors = validate_plan_events(events)
    if errors:
        raise PlanProtocolError("migration requires a valid existing event chain: " + "; ".join(errors))
    previous_head = events[-1]["event_sha256"] if events else ZERO_HASH
    migrated = copy.deepcopy(manifest)
    previous_workflow_version = migrated.get("workflow_version")
    if persist_activation:
        activation_path = protocol_activation_receipt_path(migrated.get("run_id"))
        if activation_path.exists():
            try:
                existing = json.loads(activation_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise PlanProtocolError(
                    "stranded v2 activation receipt is unreadable"
                ) from exc
            binds_workflow_identity = activation_receipt_binds_workflow_identity(
                existing
            )
            if not binds_workflow_identity:
                raise PlanProtocolError(
                    "legacy stranded activation receipt is quarantined; "
                    "authenticated workflow-identity upgrade required"
                )
            stable_bindings = [
                "run_id",
                "parent_thread_id",
                "repo_root",
                "starting_commit",
                "run_started_at",
            ]
            stable_bindings.extend(("mode", "goal"))
            if not isinstance(existing, dict) or any(
                existing.get(field) != migrated.get(field)
                for field in stable_bindings
            ):
                raise PlanProtocolError(
                    "stranded v2 activation receipt does not match the legacy manifest"
                )
            recorded_at = recorded_at or existing.get("activated_at")
            event_id = event_id or existing.get("activation_event_id")
    migrated["workflow_version"] = WORKFLOW_VERSION_V2
    migrated["plan_protocol_version"] = PLAN_PROTOCOL_V2
    migration_event = append_plan_event(
        migrated.setdefault("plan_events", []),
        "protocol_migrated",
        {
            "from_version": PLAN_PROTOCOL_V1,
            "to_version": PLAN_PROTOCOL_V2,
            "from_workflow_version": previous_workflow_version,
            "to_workflow_version": WORKFLOW_VERSION_V2,
            "previous_event_sha256": previous_head,
        },
        recorded_at=recorded_at,
        event_id=event_id,
    )
    if persist_activation:
        record_protocol_activation(migrated, migration_event)
    manifest.clear()
    manifest.update(migrated)
    return manifest
