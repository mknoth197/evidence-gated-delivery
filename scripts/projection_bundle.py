#!/usr/bin/env python3
"""Pure schemas and builders for projection preparation and transaction evidence.

The prepared bundle and the transaction receipt deliberately have separate
identities.  Evidence gathered after preparation is never an input to the
prepared digest.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from types import MappingProxyType
from typing import Any, Iterable, Mapping


PROJECTION_BUNDLE_SCHEMA_VERSION = "projection-bundle/v1"
PROJECTION_TRANSACTION_RECEIPT_SCHEMA_VERSION = (
    "projection-transaction-receipt/v1"
)
PROJECTION_KERNEL_VERSION = "projection-kernel/v1"
ASSURANCE_POLICY_VERSION = "assurance-policy/v1"

SUPPORTED_PROJECTION_SCHEMA_VERSIONS = frozenset(
    (
        PROJECTION_BUNDLE_SCHEMA_VERSION,
        PROJECTION_TRANSACTION_RECEIPT_SCHEMA_VERSION,
    )
)
SUPPORTED_PROJECTION_KERNEL_VERSIONS = frozenset((PROJECTION_KERNEL_VERSION,))
PROJECTION_SLOT_STATES = frozenset(("present", "omitted", "pending", "blocked"))
PROJECTION_SLOT_OUTCOME_STATES = frozenset(
    ("verified", "omitted", "pending", "blocked")
)
PROJECTION_TRANSACTION_FINAL_STATES = frozenset(("sealed", "blocked"))
PROJECTION_EXTERNAL_ACTION_STATES = frozenset(
    ("not_started", "started", "verified", "blocked")
)
PROJECTION_TRANSACTION_BLOCKER_CODES = frozenset(
    (
        "BLOCKED_PROJECTION_CONFLICT",
        "BLOCKED_UNSUPPORTED_SCHEMA",
        "BLOCKED_MISSING_REQUIRED_SLOT",
        "BLOCKED_REMOTE_READBACK_MISMATCH",
        "BLOCKED_REQUIRED_ESCALATION",
        "BLOCKED_PROVIDER_EVIDENCE",
        "BLOCKED_INSTALLED_SKILL_DRIFT",
        "BLOCKED_MISSING_AUTHORITY",
    )
)

# Aliases keep the public vocabulary easy to discover without weakening it.
PROJECTION_BUNDLE_V1 = PROJECTION_BUNDLE_SCHEMA_VERSION
PROJECTION_TRANSACTION_RECEIPT_V1 = PROJECTION_TRANSACTION_RECEIPT_SCHEMA_VERSION
SLOT_STATES = PROJECTION_SLOT_STATES
TRANSACTION_BLOCKER_CODES = PROJECTION_TRANSACTION_BLOCKER_CODES

PROJECTION_SCHEMA_REGISTRY = MappingProxyType(
    {
        PROJECTION_BUNDLE_SCHEMA_VERSION: "validate_projection_bundle",
        PROJECTION_TRANSACTION_RECEIPT_SCHEMA_VERSION: (
            "validate_projection_transaction_receipt"
        ),
    }
)

_HEX_DIGEST_LENGTH = 64
_BUNDLE_FIELDS = frozenset(
    (
        "schema_version",
        "bundle_id",
        "authority",
        "versions",
        "policy_versions",
        "assurance",
        "capsule_generation",
        "slots",
        "parent_bundle",
        "prepared_at",
        "prepared_digest",
    )
)
_RECEIPT_FIELDS = frozenset(
    (
        "schema_version",
        "transaction_id",
        "bundle_id",
        "prepared_digest",
        "intent",
        "audit_receipts",
        "provider_receipts",
        "graph_operations",
        "gate_outcomes",
        "external_actions",
        "slot_outcomes",
        "final_state",
        "blockers",
        "completed_at",
        "receipt_digest",
    )
)
_AUTHORITY_FIELDS = frozenset(
    ("kind", "locator", "bytes_digest", "source_revision", "byte_length", "sidecar")
)
_VERSION_FIELDS = frozenset(("kernel", "reader", "canonicalizer"))
_ASSURANCE_FIELDS = frozenset(
    ("requested", "effective", "selection_origin", "legacy_subprofile")
)
_CAPSULE_FIELDS = frozenset(("capsule_id", "generation", "digest"))
_PARENT_FIELDS = frozenset(("bundle_id", "prepared_digest"))
_INTENT_FIELDS = frozenset(
    ("risk_classification", "authority_ref", "staged_action_digest")
)
_EXTERNAL_ACTION_FIELDS = frozenset(
    (
        "target",
        "started_evidence",
        "mutation_receipt",
        "readback_evidence",
        "durable_output",
        "state",
    )
)
_SLOT_FIELDS = {
    "present": frozenset(
        ("state", "payload_digest", "projection_version", "payload", "locator")
    ),
    "omitted": frozenset(
        ("state", "policy_rule_id", "reason", "evidence_refs")
    ),
    "pending": frozenset(("state", "responsible_component", "next_action")),
    "blocked": frozenset(
        ("state", "blocker_ref", "responsible_component", "next_safe_action")
    ),
}


class ProjectionBundleError(ValueError):
    """Raised when projection evidence violates a closed protocol invariant."""


def canonical_projection_json(value: Any) -> str:
    """Return strict canonical JSON used by every projection digest."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProjectionBundleError(f"projection value is not canonical JSON: {exc}") from exc


def projection_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_projection_json(value).encode("utf-8")).hexdigest()


def authority_bytes_sha256(authority_bytes: bytes) -> str:
    if not isinstance(authority_bytes, bytes):
        raise ProjectionBundleError("authority bytes must be an immutable bytes value")
    return hashlib.sha256(authority_bytes).hexdigest()


def bundle_id_for_digest(prepared_digest: str) -> str:
    if not _is_digest(prepared_digest):
        raise ProjectionBundleError("prepared_digest must be a lowercase SHA-256")
    return f"pb1-{prepared_digest[:12]}"


def transaction_id_for_digest(receipt_digest: str) -> str:
    if not _is_digest(receipt_digest):
        raise ProjectionBundleError("receipt_digest must be a lowercase SHA-256")
    return f"ptx1-{receipt_digest[:12]}"


def prepared_bundle_digest(bundle: Mapping[str, Any]) -> str:
    payload = {
        key: copy.deepcopy(value)
        for key, value in bundle.items()
        if key not in {"bundle_id", "prepared_digest"}
    }
    return projection_sha256(payload)


def transaction_receipt_digest(receipt: Mapping[str, Any]) -> str:
    payload = {
        key: copy.deepcopy(value)
        for key, value in receipt.items()
        if key not in {"transaction_id", "receipt_digest"}
    }
    return projection_sha256(payload)


def build_projection_bundle(
    authority_bytes: bytes,
    *,
    authority: Mapping[str, Any],
    versions: Mapping[str, Any],
    policy_versions: Mapping[str, Any],
    assurance: Mapping[str, Any],
    capsule_generation: Mapping[str, Any],
    slots: Mapping[str, Any],
    prepared_at: str,
    parent_bundle: Mapping[str, Any] | None = None,
    required_slots: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build a deterministic prepared bundle bound to one immutable byte buffer."""

    if not isinstance(authority_bytes, bytes):
        raise ProjectionBundleError("authority bytes must be an immutable bytes value")
    frozen = authority_bytes
    authority_value = copy.deepcopy(dict(authority))
    claimed_digest = authority_value.get("bytes_digest")
    observed_digest = authority_bytes_sha256(frozen)
    if claimed_digest not in (None, observed_digest):
        raise ProjectionBundleError(
            "authority.bytes_digest conflict: "
            f"expected {observed_digest}, observed {claimed_digest}"
        )
    claimed_length = authority_value.get("byte_length")
    if claimed_length not in (None, len(frozen)):
        raise ProjectionBundleError(
            "authority.byte_length conflict: "
            f"expected {len(frozen)}, observed {claimed_length}"
        )
    authority_value["bytes_digest"] = observed_digest
    authority_value["byte_length"] = len(frozen)
    authority_value.setdefault("sidecar", None)

    bundle: dict[str, Any] = {
        "schema_version": PROJECTION_BUNDLE_SCHEMA_VERSION,
        "bundle_id": "",
        "authority": authority_value,
        "versions": copy.deepcopy(dict(versions)),
        "policy_versions": copy.deepcopy(dict(policy_versions)),
        "assurance": copy.deepcopy(dict(assurance)),
        "capsule_generation": copy.deepcopy(dict(capsule_generation)),
        "slots": copy.deepcopy(dict(slots)),
        "parent_bundle": copy.deepcopy(dict(parent_bundle)) if parent_bundle else None,
        "prepared_at": prepared_at,
        "prepared_digest": "",
    }
    errors = _validate_prepared_content(bundle, required_slots=required_slots)
    if errors:
        raise ProjectionBundleError("; ".join(errors))
    digest = prepared_bundle_digest(bundle)
    bundle["prepared_digest"] = digest
    bundle["bundle_id"] = bundle_id_for_digest(digest)
    return bundle


def validate_projection_bundle(
    bundle: Any,
    *,
    authority_bytes: bytes | None = None,
    required_slots: Iterable[str] | None = None,
) -> list[str]:
    """Validate prepared identity and optionally bind it to the original bytes."""

    if not isinstance(bundle, dict):
        return ["projection bundle must be an object"]
    errors = _exact_fields(bundle, _BUNDLE_FIELDS, "projection bundle")
    errors.extend(_validate_prepared_content(bundle, required_slots=required_slots))
    digest = bundle.get("prepared_digest")
    if not _is_digest(digest):
        errors.append("projection bundle prepared_digest must be a lowercase SHA-256")
    else:
        computed = prepared_bundle_digest(bundle)
        if digest != computed:
            errors.append(
                "projection bundle prepared_digest conflict: "
                f"expected {computed}, observed {digest}"
            )
        expected_id = bundle_id_for_digest(digest)
        if bundle.get("bundle_id") != expected_id:
            errors.append(
                "projection bundle bundle_id conflict: "
                f"expected {expected_id}, observed {bundle.get('bundle_id')}"
            )
    if authority_bytes is not None:
        if not isinstance(authority_bytes, bytes):
            errors.append("authority bytes must be an immutable bytes value")
        else:
            observed = authority_bytes_sha256(authority_bytes)
            claimed = (
                bundle.get("authority", {}).get("bytes_digest")
                if isinstance(bundle.get("authority"), dict)
                else None
            )
            if claimed != observed:
                errors.append(
                    "projection bundle authority.bytes_digest conflict: "
                    f"expected {observed}, observed {claimed}"
                )
    return errors


def validate_projection_slot(name: str, slot: Any) -> list[str]:
    """Validate one named slot against the closed v1 state vocabulary."""

    if not _nonempty(name):
        return ["projection slot name must be a non-empty string"]
    return _validate_slot(name, slot)


def build_projection_transaction_receipt(
    bundle: Mapping[str, Any],
    *,
    intent: Mapping[str, Any],
    completed_at: str,
    audit_receipts: Iterable[Any] = (),
    provider_receipts: Iterable[Any] = (),
    graph_operations: Iterable[Any] = (),
    gate_outcomes: Iterable[Any] = (),
    external_actions: Iterable[Any] = (),
    slot_outcomes: Mapping[str, Any] | None = None,
    blockers: Iterable[Mapping[str, Any]] = (),
    required_slots: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build a sealed or durably blocked receipt without altering the bundle."""

    prepared = copy.deepcopy(dict(bundle))
    bundle_errors = validate_projection_bundle(prepared, required_slots=required_slots)
    detected: list[dict[str, Any]] = []
    if bundle_errors:
        detected.append(
            _blocker(
                "BLOCKED_UNSUPPORTED_SCHEMA",
                evidence_refs=bundle_errors,
                next_safe_action="Repair and re-prepare the projection bundle",
            )
        )

    slots = prepared.get("slots") if isinstance(prepared.get("slots"), dict) else {}
    outcomes = (
        copy.deepcopy(dict(slot_outcomes))
        if slot_outcomes is not None
        else {
            name: {
                "state": (
                    "verified"
                    if slot.get("state") == "present"
                    else slot.get("state")
                ),
                "prepared_state": slot.get("state"),
            }
            for name, slot in slots.items()
            if isinstance(slot, dict)
        }
    )
    detected.extend(_slot_seal_blockers(slots, outcomes, required_slots=required_slots))

    actions = copy.deepcopy(list(external_actions))
    detected.extend(_external_action_blockers(actions))
    supplied_blockers = copy.deepcopy(list(blockers))
    detected.extend(supplied_blockers)

    final_state = "blocked" if detected else "sealed"
    receipt: dict[str, Any] = {
        "schema_version": PROJECTION_TRANSACTION_RECEIPT_SCHEMA_VERSION,
        "transaction_id": "",
        "bundle_id": prepared.get("bundle_id"),
        "prepared_digest": prepared.get("prepared_digest"),
        "intent": copy.deepcopy(dict(intent)),
        "audit_receipts": copy.deepcopy(list(audit_receipts)),
        "provider_receipts": copy.deepcopy(list(provider_receipts)),
        "graph_operations": copy.deepcopy(list(graph_operations)),
        "gate_outcomes": copy.deepcopy(list(gate_outcomes)),
        "external_actions": actions,
        "slot_outcomes": outcomes,
        "final_state": final_state,
        "blockers": detected,
        "completed_at": completed_at,
        "receipt_digest": "",
    }
    content_errors = _validate_receipt_content(receipt, prepared)
    if content_errors:
        # Builders fail only for malformed caller input. Runtime conflicts are
        # represented above as a durable blocked receipt.
        raise ProjectionBundleError("; ".join(content_errors))
    digest = transaction_receipt_digest(receipt)
    receipt["receipt_digest"] = digest
    receipt["transaction_id"] = transaction_id_for_digest(digest)
    return receipt


def validate_projection_transaction_receipt(
    receipt: Any,
    *,
    bundle: Mapping[str, Any] | None = None,
    required_slots: Iterable[str] | None = None,
) -> list[str]:
    if not isinstance(receipt, dict):
        return ["projection transaction receipt must be an object"]
    errors = _exact_fields(receipt, _RECEIPT_FIELDS, "projection transaction receipt")
    errors.extend(_validate_receipt_content(receipt, bundle, required_slots=required_slots))
    digest = receipt.get("receipt_digest")
    if not _is_digest(digest):
        errors.append("projection transaction receipt_digest must be a lowercase SHA-256")
    else:
        computed = transaction_receipt_digest(receipt)
        if digest != computed:
            errors.append(
                "projection transaction receipt_digest conflict: "
                f"expected {computed}, observed {digest}"
            )
        expected_id = transaction_id_for_digest(digest)
        if receipt.get("transaction_id") != expected_id:
            errors.append(
                "projection transaction transaction_id conflict: "
                f"expected {expected_id}, observed {receipt.get('transaction_id')}"
            )
    return errors


def _validate_prepared_content(
    bundle: Mapping[str, Any], *, required_slots: Iterable[str] | None
) -> list[str]:
    errors: list[str] = []
    if bundle.get("schema_version") != PROJECTION_BUNDLE_SCHEMA_VERSION:
        errors.append(
            "projection bundle schema_version is unsupported: "
            f"{bundle.get('schema_version')!r}"
        )
    authority = bundle.get("authority")
    if not isinstance(authority, dict):
        errors.append("projection bundle authority must be an object")
    else:
        errors.extend(_closed_fields(authority, _AUTHORITY_FIELDS, "authority"))
        for field in ("kind", "locator", "source_revision"):
            if not _nonempty(authority.get(field)):
                errors.append(f"authority.{field} must be a non-empty string")
        if not _is_digest(authority.get("bytes_digest")):
            errors.append("authority.bytes_digest must be a lowercase SHA-256")
        length = authority.get("byte_length")
        if isinstance(length, bool) or not isinstance(length, int) or length < 0:
            errors.append("authority.byte_length must be a non-negative integer")
        sidecar = authority.get("sidecar")
        if sidecar is not None and not isinstance(sidecar, dict):
            errors.append("authority.sidecar must be null or an object")

    versions = bundle.get("versions")
    if not isinstance(versions, dict):
        errors.append("projection bundle versions must be an object")
    else:
        errors.extend(_exact_fields(versions, _VERSION_FIELDS, "versions"))
        for field in _VERSION_FIELDS:
            if not _nonempty(versions.get(field)):
                errors.append(f"versions.{field} must be a non-empty string")
        if versions.get("kernel") not in SUPPORTED_PROJECTION_KERNEL_VERSIONS:
            errors.append(f"versions.kernel is unsupported: {versions.get('kernel')!r}")

    policy_versions = bundle.get("policy_versions")
    if not isinstance(policy_versions, dict) or not policy_versions:
        errors.append("projection bundle policy_versions must be a non-empty object")
    else:
        for name, version in policy_versions.items():
            if not _nonempty(name) or not _nonempty(version):
                errors.append("policy_versions names and values must be non-empty strings")
        if policy_versions.get("assurance") != ASSURANCE_POLICY_VERSION:
            errors.append(
                "policy_versions.assurance must be exactly assurance-policy/v1"
            )

    assurance = bundle.get("assurance")
    if not isinstance(assurance, dict):
        errors.append("projection bundle assurance must be an object")
    else:
        errors.extend(_closed_fields(assurance, _ASSURANCE_FIELDS, "assurance"))
        for field in ("requested", "effective", "selection_origin"):
            if not _nonempty(assurance.get(field)):
                errors.append(f"assurance.{field} must be a non-empty string")
        if assurance.get("effective") not in {"light", "heavy"}:
            errors.append("assurance.effective must be light or heavy")
        legacy = assurance.get("legacy_subprofile")
        if legacy is not None and legacy not in {"quick", "balanced", "deep"}:
            errors.append("assurance.legacy_subprofile is unsupported")

    capsule = bundle.get("capsule_generation")
    if not isinstance(capsule, dict):
        errors.append("projection bundle capsule_generation must be an object")
    else:
        errors.extend(_exact_fields(capsule, _CAPSULE_FIELDS, "capsule_generation"))
        if not _nonempty(capsule.get("capsule_id")):
            errors.append("capsule_generation.capsule_id must be a non-empty string")
        generation = capsule.get("generation")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            errors.append("capsule_generation.generation must be a positive integer")
        if not _is_digest(capsule.get("digest")):
            errors.append("capsule_generation.digest must be a lowercase SHA-256")

    slots = bundle.get("slots")
    if not isinstance(slots, dict):
        errors.append("projection bundle slots must be an object")
    else:
        expected_slots, slot_errors = _slot_names(required_slots)
        errors.extend(slot_errors)
        if expected_slots is not None:
            missing = sorted(expected_slots - set(slots))
            unknown = sorted(set(slots) - expected_slots)
            if missing:
                errors.append(
                    "BLOCKED_MISSING_REQUIRED_SLOT: missing slots " + ", ".join(missing)
                )
            if unknown:
                errors.append(
                    "BLOCKED_UNSUPPORTED_SCHEMA: unknown slots " + ", ".join(unknown)
                )
        for name, slot in slots.items():
            if not _nonempty(name):
                errors.append("projection slot names must be non-empty strings")
                continue
            errors.extend(_validate_slot(name, slot))

    parent = bundle.get("parent_bundle")
    if parent is not None:
        if not isinstance(parent, dict):
            errors.append("parent_bundle must be null or an object")
        else:
            errors.extend(_exact_fields(parent, _PARENT_FIELDS, "parent_bundle"))
            if not _nonempty(parent.get("bundle_id")):
                errors.append("parent_bundle.bundle_id must be a non-empty string")
            if not _is_digest(parent.get("prepared_digest")):
                errors.append("parent_bundle.prepared_digest must be a lowercase SHA-256")
            elif parent.get("bundle_id") != bundle_id_for_digest(parent["prepared_digest"]):
                errors.append("parent_bundle identity conflicts with its prepared_digest")
    if not _timezone_aware(bundle.get("prepared_at")):
        errors.append("projection bundle prepared_at must be timezone-aware ISO-8601")
    return errors


def _validate_slot(name: str, slot: Any) -> list[str]:
    prefix = f"slots.{name}"
    if not isinstance(slot, dict):
        return [f"{prefix} must be an object"]
    state = slot.get("state")
    if state not in PROJECTION_SLOT_STATES:
        return [
            f"BLOCKED_UNSUPPORTED_SCHEMA: {prefix}.state is unsupported: {state!r}"
        ]
    errors = _closed_fields(slot, _SLOT_FIELDS[state], prefix)
    if state == "present":
        if not _is_digest(slot.get("payload_digest")):
            errors.append(f"{prefix}.payload_digest must be a lowercase SHA-256")
        if not _nonempty(slot.get("projection_version")):
            errors.append(f"{prefix}.projection_version must be a non-empty string")
        if "payload" not in slot and not _nonempty(slot.get("locator")):
            errors.append(f"{prefix} requires typed payload or content-addressed locator")
        if "payload" in slot:
            try:
                payload_digest = projection_sha256(slot["payload"])
            except ProjectionBundleError as exc:
                errors.append(f"{prefix}.payload is invalid: {exc}")
            else:
                if slot.get("payload_digest") != payload_digest:
                    errors.append(
                        f"{prefix}.payload_digest conflict: expected {payload_digest}, "
                        f"observed {slot.get('payload_digest')}"
                    )
    elif state == "omitted":
        for field in ("policy_rule_id", "reason"):
            if not _nonempty(slot.get(field)):
                errors.append(f"{prefix}.{field} must be a non-empty string")
        refs = slot.get("evidence_refs")
        if not isinstance(refs, list) or not refs or not all(_nonempty(item) for item in refs):
            errors.append(f"{prefix}.evidence_refs must contain non-empty strings")
    elif state == "pending":
        for field in ("responsible_component", "next_action"):
            if not _nonempty(slot.get(field)):
                errors.append(f"{prefix}.{field} must be a non-empty string")
    elif state == "blocked":
        for field in ("blocker_ref", "responsible_component", "next_safe_action"):
            if not _nonempty(slot.get(field)):
                errors.append(f"{prefix}.{field} must be a non-empty string")
    return errors


def _validate_receipt_content(
    receipt: Mapping[str, Any],
    bundle: Mapping[str, Any] | None,
    *,
    required_slots: Iterable[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    if receipt.get("schema_version") != PROJECTION_TRANSACTION_RECEIPT_SCHEMA_VERSION:
        errors.append(
            "projection transaction schema_version is unsupported: "
            f"{receipt.get('schema_version')!r}"
        )
    if bundle is not None:
        errors.extend(validate_projection_bundle(bundle, required_slots=required_slots))
        for field in ("bundle_id", "prepared_digest"):
            if receipt.get(field) != bundle.get(field):
                errors.append(
                    f"projection transaction {field} conflict: expected "
                    f"{bundle.get(field)}, observed {receipt.get(field)}"
                )
    else:
        if not _nonempty(receipt.get("bundle_id")):
            errors.append("projection transaction bundle_id must be a non-empty string")
        if not _is_digest(receipt.get("prepared_digest")):
            errors.append("projection transaction prepared_digest must be a lowercase SHA-256")

    intent = receipt.get("intent")
    if not isinstance(intent, dict):
        errors.append("projection transaction intent must be an object")
    else:
        errors.extend(_exact_fields(intent, _INTENT_FIELDS, "intent"))
        for field in ("risk_classification", "authority_ref"):
            if not _nonempty(intent.get(field)):
                errors.append(f"intent.{field} must be a non-empty string")
        if not _is_digest(intent.get("staged_action_digest")):
            errors.append("intent.staged_action_digest must be a lowercase SHA-256")
    for field in (
        "audit_receipts",
        "provider_receipts",
        "graph_operations",
        "gate_outcomes",
        "external_actions",
        "blockers",
    ):
        if not isinstance(receipt.get(field), list):
            errors.append(f"projection transaction {field} must be an array")

    actions = receipt.get("external_actions")
    if isinstance(actions, list):
        for index, action in enumerate(actions):
            errors.extend(_validate_external_action(action, index))

    outcomes = receipt.get("slot_outcomes")
    if not isinstance(outcomes, dict):
        errors.append("projection transaction slot_outcomes must be an object")
    else:
        prepared_slots = (
            bundle.get("slots") if isinstance(bundle, dict) and isinstance(bundle.get("slots"), dict)
            else None
        )
        if prepared_slots is not None and set(outcomes) != set(prepared_slots):
            errors.append(
                "projection transaction slot_outcomes must exactly cover prepared slots"
            )
        for name, outcome in outcomes.items():
            if not isinstance(outcome, dict):
                errors.append(f"slot_outcomes.{name} must be an object")
                continue
            state = outcome.get("state")
            if state not in PROJECTION_SLOT_OUTCOME_STATES:
                errors.append(
                    "BLOCKED_UNSUPPORTED_SCHEMA: "
                    f"slot_outcomes.{name}.state is unsupported: {state!r}"
                )

    final_state = receipt.get("final_state")
    if final_state not in PROJECTION_TRANSACTION_FINAL_STATES:
        errors.append(f"projection transaction final_state is unsupported: {final_state!r}")
    blockers = receipt.get("blockers")
    if isinstance(blockers, list):
        for index, blocker in enumerate(blockers):
            errors.extend(_validate_blocker(blocker, index))
        if final_state == "sealed" and blockers:
            errors.append("sealed projection transaction cannot contain blockers")
        if final_state == "blocked" and not blockers:
            errors.append("blocked projection transaction requires at least one blocker")

    if final_state == "sealed":
        actions = receipt.get("external_actions")
        if isinstance(actions, list) and any(
            isinstance(action, dict) and action.get("state") != "verified"
            for action in actions
        ):
            errors.append("sealed projection transaction requires verified external actions")
        if isinstance(outcomes, dict) and any(
            not isinstance(outcome, dict)
            or outcome.get("state") not in {"verified", "omitted"}
            for outcome in outcomes.values()
        ):
            errors.append("sealed projection transaction has non-sealable slot outcomes")
    if not _timezone_aware(receipt.get("completed_at")):
        errors.append("projection transaction completed_at must be timezone-aware ISO-8601")
    return errors


def _slot_seal_blockers(
    slots: Mapping[str, Any],
    outcomes: Mapping[str, Any],
    *,
    required_slots: Iterable[str] | None,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    expected, errors = _slot_names(required_slots)
    if errors:
        return [
            _blocker(
                "BLOCKED_UNSUPPORTED_SCHEMA",
                evidence_refs=errors,
                next_safe_action="Correct the policy required-slot vocabulary",
            )
        ]
    if expected is None:
        expected = set(slots)
    missing = sorted(expected - set(slots))
    if missing:
        blockers.append(
            _blocker(
                "BLOCKED_MISSING_REQUIRED_SLOT",
                evidence_refs=[f"missing slot {name}" for name in missing],
                next_safe_action="Prepare every policy-required projection slot",
            )
        )
    unknown = sorted(set(slots) - expected)
    if unknown:
        blockers.append(
            _blocker(
                "BLOCKED_UNSUPPORTED_SCHEMA",
                evidence_refs=[f"unknown slot {name}" for name in unknown],
                next_safe_action="Use the policy's closed projection-slot vocabulary",
            )
        )
    for name in sorted(expected & set(slots)):
        slot = slots.get(name)
        state = slot.get("state") if isinstance(slot, dict) else None
        outcome = outcomes.get(name)
        outcome_state = outcome.get("state") if isinstance(outcome, dict) else None
        if state in {"pending", "blocked"} or outcome_state in {"pending", "blocked"}:
            blockers.append(
                _blocker(
                    "BLOCKED_MISSING_REQUIRED_SLOT",
                    evidence_refs=[
                        f"slot {name} prepared_state={state!r} outcome_state={outcome_state!r}"
                    ],
                    next_safe_action=f"Resolve required projection slot {name}",
                )
            )
    return blockers


def _external_action_blockers(actions: list[Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            blockers.append(
                _blocker(
                    "BLOCKED_UNSUPPORTED_SCHEMA",
                    evidence_refs=[f"external_actions[{index}] is not an object"],
                    next_safe_action="Correct the external-action evidence",
                )
            )
            continue
        state = action.get("state")
        if state not in PROJECTION_EXTERNAL_ACTION_STATES:
            blockers.append(
                _blocker(
                    "BLOCKED_UNSUPPORTED_SCHEMA",
                    evidence_refs=[
                        f"external_actions[{index}].state is unsupported: {state!r}"
                    ],
                    next_safe_action="Use the closed external-action state vocabulary",
                )
            )
        elif state != "verified" or not all(
            _nonempty(action.get(field))
            for field in (
                "started_evidence",
                "mutation_receipt",
                "readback_evidence",
                "durable_output",
            )
        ):
            blockers.append(
                _blocker(
                    "BLOCKED_REMOTE_READBACK_MISMATCH",
                    evidence_refs=[
                        f"external_actions[{index}] target={action.get('target')!r} state={state!r}"
                    ],
                    next_safe_action="Read back the remote target and reconcile exact state",
                )
            )
    return blockers


def _validate_external_action(action: Any, index: int) -> list[str]:
    prefix = f"external_actions[{index}]"
    if not isinstance(action, dict):
        return [f"{prefix} must be an object"]
    errors = _exact_fields(action, _EXTERNAL_ACTION_FIELDS, prefix)
    if not _nonempty(action.get("target")):
        errors.append(f"{prefix}.target must be a non-empty string")
    state = action.get("state")
    if state not in PROJECTION_EXTERNAL_ACTION_STATES:
        errors.append(f"BLOCKED_UNSUPPORTED_SCHEMA: {prefix}.state is unsupported: {state!r}")
    if state in {"started", "verified", "blocked"} and not _nonempty(
        action.get("started_evidence")
    ):
        errors.append(f"{prefix}.started_evidence is required for state {state!r}")
    if state == "verified":
        for field in ("mutation_receipt", "readback_evidence", "durable_output"):
            if not _nonempty(action.get(field)):
                errors.append(f"{prefix}.{field} is required for verified state")
    return errors


def _validate_blocker(blocker: Any, index: int) -> list[str]:
    prefix = f"blockers[{index}]"
    if not isinstance(blocker, dict):
        return [f"{prefix} must be an object"]
    errors: list[str] = []
    if blocker.get("code") not in PROJECTION_TRANSACTION_BLOCKER_CODES:
        errors.append(
            f"BLOCKED_UNSUPPORTED_SCHEMA: {prefix}.code is unsupported: "
            f"{blocker.get('code')!r}"
        )
    refs = blocker.get("evidence_refs")
    if not isinstance(refs, list) or not refs or not all(_nonempty(ref) for ref in refs):
        errors.append(f"{prefix}.evidence_refs must contain non-empty strings")
    if not _nonempty(blocker.get("next_safe_action")):
        errors.append(f"{prefix}.next_safe_action must be a non-empty string")
    return errors


def _blocker(
    code: str, *, evidence_refs: Iterable[str], next_safe_action: str, **details: Any
) -> dict[str, Any]:
    return {
        "code": code,
        "evidence_refs": list(evidence_refs),
        "next_safe_action": next_safe_action,
        **copy.deepcopy(details),
    }


def _slot_names(
    required_slots: Iterable[str] | None,
) -> tuple[set[str] | None, list[str]]:
    if required_slots is None:
        return None, []
    if isinstance(required_slots, (str, bytes)):
        return None, ["required_slots must be an iterable of unique non-empty names"]
    values = list(required_slots)
    if not all(_nonempty(item) for item in values):
        return None, ["required_slots must contain only non-empty strings"]
    if len(set(values)) != len(values):
        return None, ["required_slots must contain unique names"]
    return set(values), []


def _closed_fields(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> list[str]:
    unknown = sorted(set(value) - allowed)
    errors: list[str] = []
    if unknown:
        errors.append(
            f"BLOCKED_UNSUPPORTED_SCHEMA: {label} has unknown fields: {', '.join(unknown)}"
        )
    return errors


def _exact_fields(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> list[str]:
    errors = _closed_fields(value, allowed, label)
    missing = sorted(allowed - set(value))
    if missing:
        errors.insert(0, f"{label} is missing required fields: {', '.join(missing)}")
    return errors


def _is_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _HEX_DIGEST_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _timezone_aware(value: Any) -> bool:
    if not _nonempty(value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None
