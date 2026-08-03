#!/usr/bin/env python3
"""Single-read projection kernel and atomic envelope persistence."""

from __future__ import annotations

import copy
import os
import re
import tempfile
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from projection_bundle import (
    PROJECTION_KERNEL_VERSION,
    authority_bytes_sha256,
    build_projection_bundle,
    build_projection_transaction_receipt,
    canonical_projection_json,
    projection_sha256,
    validate_projection_bundle,
    validate_projection_slot,
    validate_projection_transaction_receipt,
)


ProjectionAdapter = Callable[[bytes, str, Mapping[str, str]], Mapping[str, Any]]


class ProjectionKernelError(ValueError):
    """Raised for malformed kernel inputs, not adapter conflicts."""


def run_projection_kernel(
    authority_bytes: bytes,
    *,
    authority: Mapping[str, Any],
    versions: Mapping[str, str],
    policy_versions: Mapping[str, str],
    assurance: Mapping[str, Any],
    capsule_generation: Mapping[str, Any],
    adapters: Mapping[str, ProjectionAdapter],
    required_slots: Iterable[str],
    prepared_at: str,
    completed_at: str,
    intent: Mapping[str, Any],
    explicit_slots: Mapping[str, Mapping[str, Any]] | None = None,
    parent_bundle: Mapping[str, Any] | None = None,
    audit_receipts: Iterable[Any] = (),
    provider_receipts: Iterable[Any] = (),
    graph_operations: Iterable[Any] = (),
    gate_outcomes: Iterable[Any] = (),
    external_actions: Iterable[Any] = (),
) -> dict[str, dict[str, Any]]:
    """Run every adapter against the exact same frozen bytes and version claims.

    Adapter conflicts are data, not exceptions: the returned envelope contains a
    prepared bundle with the affected slot blocked and a separately content-
    addressed blocked receipt.  No adapter result is ever reported as a partial
    transaction success.
    """

    frozen = _freeze_authority(authority_bytes)
    digest = authority_bytes_sha256(frozen)
    version_claims = copy.deepcopy(dict(versions))
    if version_claims.get("kernel") != PROJECTION_KERNEL_VERSION:
        raise ProjectionKernelError(
            f"versions.kernel must be exactly {PROJECTION_KERNEL_VERSION}"
        )
    required = _required_slot_names(required_slots)
    adapter_map = dict(adapters)
    explicit = copy.deepcopy(dict(explicit_slots or {}))
    overlap = set(adapter_map) & set(explicit)
    if overlap:
        raise ProjectionKernelError(
            "projection slots cannot be supplied by both adapter and explicit_slots: "
            + ", ".join(sorted(overlap))
        )

    slots: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    unknown = sorted((set(adapter_map) | set(explicit)) - set(required))
    if unknown:
        for name in unknown:
            conflicts.append(
                _kernel_blocker(
                    "BLOCKED_UNSUPPORTED_SCHEMA",
                    name,
                    [
                        {
                            "field": "slot_name",
                            "expected": sorted(required),
                            "observed": name,
                        }
                    ],
                    "Use the policy's closed required-slot vocabulary",
                )
            )

    for name in required:
        if name in explicit:
            slot_errors = validate_projection_slot(name, explicit[name])
            if slot_errors:
                conflict = _kernel_blocker(
                    "BLOCKED_UNSUPPORTED_SCHEMA",
                    name,
                    [
                        {
                            "field": f"slots.{name}",
                            "expected": "supported closed slot vocabulary",
                            "observed": error,
                        }
                        for error in slot_errors
                    ],
                    f"Correct the explicit {name} slot vocabulary",
                )
                conflicts.append(conflict)
                slots[name] = _blocked_slot(name, conflict)
            else:
                slots[name] = explicit[name]
            continue
        adapter = adapter_map.get(name)
        if adapter is None:
            conflicts.append(
                _kernel_blocker(
                    "BLOCKED_MISSING_REQUIRED_SLOT",
                    name,
                    [{"field": "adapter", "expected": name, "observed": None}],
                    f"Provide the required {name} projection adapter or explicit omission",
                )
            )
            slots[name] = _blocked_slot(name, conflicts[-1])
            continue
        try:
            result = adapter(frozen, digest, MappingProxyType(version_claims))
        except Exception as exc:  # adapter failures must become durable evidence
            conflict = _kernel_blocker(
                "BLOCKED_PROJECTION_CONFLICT",
                name,
                [
                    {
                        "field": "adapter_execution",
                        "expected": "deterministic projection result",
                        # Adapter exception text is untrusted and may contain a
                        # credential, private payload, or unbounded response.
                        "observed": {"exception_type": type(exc).__name__},
                    }
                ],
                f"Repair and rerun the {name} projection adapter",
            )
            conflicts.append(conflict)
            slots[name] = _blocked_slot(name, conflict)
            continue
        slot, adapter_conflicts = _normalize_adapter_result(
            name, result, authority_digest=digest, versions=version_claims
        )
        if adapter_conflicts:
            conflict = _kernel_blocker(
                "BLOCKED_PROJECTION_CONFLICT",
                name,
                adapter_conflicts,
                f"Reconcile the {name} adapter with the prepared authority and versions",
            )
            conflicts.append(conflict)
            slots[name] = _blocked_slot(name, conflict)
        else:
            slot_errors = validate_projection_slot(name, slot)
            if slot_errors:
                conflict = _kernel_blocker(
                    "BLOCKED_UNSUPPORTED_SCHEMA",
                    name,
                    [
                        {
                            "field": f"slots.{name}",
                            "expected": "supported closed slot vocabulary",
                            "observed": error,
                        }
                        for error in slot_errors
                    ],
                    f"Correct the {name} adapter slot vocabulary",
                )
                conflicts.append(conflict)
                slots[name] = _blocked_slot(name, conflict)
            else:
                slots[name] = slot

    # Unknown inputs never become prepared slots. Their exact diagnostics remain
    # in the receipt while the closed prepared schema contains required slots only.
    bundle = build_projection_bundle(
        frozen,
        authority=authority,
        versions=version_claims,
        policy_versions=policy_versions,
        assurance=assurance,
        capsule_generation=capsule_generation,
        slots=slots,
        parent_bundle=parent_bundle,
        prepared_at=prepared_at,
        required_slots=required,
    )
    receipt = build_projection_transaction_receipt(
        bundle,
        intent=intent,
        audit_receipts=audit_receipts,
        provider_receipts=provider_receipts,
        graph_operations=graph_operations,
        gate_outcomes=gate_outcomes,
        external_actions=external_actions,
        blockers=conflicts,
        completed_at=completed_at,
        required_slots=required,
    )
    return {"bundle": bundle, "receipt": receipt}


def atomic_write_projection_envelope(
    path: str | os.PathLike[str],
    bundle: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    required_slots: Iterable[str] | None = None,
) -> str:
    """Validate and atomically persist one bundle+receipt JSON envelope.

    The writer never writes the two identities separately.  A failure before
    ``os.replace`` leaves any previous envelope authoritative.
    """

    bundle_errors = validate_projection_bundle(bundle, required_slots=required_slots)
    receipt_errors = validate_projection_transaction_receipt(
        receipt, bundle=bundle, required_slots=required_slots
    )
    errors = bundle_errors + receipt_errors
    if errors:
        raise ProjectionKernelError("invalid projection envelope: " + "; ".join(errors))
    envelope = {
        "bundle": copy.deepcopy(dict(bundle)),
        "receipt": copy.deepcopy(dict(receipt)),
    }
    rendered = canonical_projection_json(envelope) + "\n"
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        expected_bytes = rendered.encode("utf-8")
        observed_bytes = target.read_bytes()
        if observed_bytes != expected_bytes:
            raise ProjectionKernelError(
                "projection envelope read-back mismatch after atomic replace: "
                f"expected sha256={_bytes_sha256(expected_bytes)}, "
                f"observed sha256={_bytes_sha256(observed_bytes)}"
            )
        directory_descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return projection_sha256(envelope)


# A concise alias for callers that treat the file as the local transaction log.
write_projection_envelope = atomic_write_projection_envelope


def _freeze_authority(value: bytes) -> bytes:
    if not isinstance(value, bytes):
        raise ProjectionKernelError("authority bytes must be supplied once as immutable bytes")
    # memoryview.tobytes makes the ownership boundary explicit even if ``value``
    # originated from a mutable buffer before the kernel call.
    return memoryview(value).tobytes()


def _required_slot_names(required_slots: Iterable[str]) -> tuple[str, ...]:
    if isinstance(required_slots, (str, bytes)):
        raise ProjectionKernelError("required_slots must be an iterable of unique names")
    values = tuple(required_slots)
    if not values or not all(isinstance(value, str) and value.strip() for value in values):
        raise ProjectionKernelError("required_slots must contain non-empty names")
    if len(set(values)) != len(values):
        raise ProjectionKernelError("required_slots must contain unique names")
    return values


def _normalize_adapter_result(
    name: str,
    result: Any,
    *,
    authority_digest: str,
    versions: Mapping[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(result, Mapping):
        return {}, [
            {
                "field": "result",
                "expected": "mapping",
                "observed": type(result).__name__,
            }
        ]
    value = copy.deepcopy(dict(result))
    claims_digest = value.pop("authority_digest", authority_digest)
    claims_versions = value.pop("versions", dict(versions))
    slot = value.pop("slot", value)
    conflicts: list[dict[str, Any]] = []
    if claims_digest != authority_digest:
        conflicts.append(
            {
                "field": "authority.bytes_digest",
                "expected": authority_digest,
                "observed": claims_digest,
            }
        )
    if not isinstance(claims_versions, Mapping):
        conflicts.append(
            {
                "field": "versions",
                "expected": dict(versions),
                "observed": claims_versions,
            }
        )
    else:
        for version_name in ("kernel", "reader", "canonicalizer"):
            observed = claims_versions.get(version_name)
            expected = versions.get(version_name)
            if observed != expected:
                conflicts.append(
                    {
                        "field": f"versions.{version_name}",
                        "expected": expected,
                        "observed": observed,
                    }
                )
    if value and "slot" in result:
        conflicts.append(
            {
                "field": "adapter_result",
                "expected": ["authority_digest", "versions", "slot"],
                "observed": sorted(result),
            }
        )
    if not isinstance(slot, dict):
        conflicts.append(
            {
                "field": f"slots.{name}",
                "expected": "mapping",
                "observed": type(slot).__name__,
            }
        )
        return {}, conflicts
    return slot, conflicts


def _kernel_blocker(
    code: str,
    projection: str,
    conflicts: list[dict[str, Any]],
    next_safe_action: str,
) -> dict[str, Any]:
    safe_projection = _safe_component_name(projection)
    safe_conflicts = [
        {
            "field": _safe_field_name(conflict.get("field")),
            "expected": _safe_diagnostic_value(conflict.get("expected")),
            "observed": _safe_diagnostic_value(conflict.get("observed")),
        }
        for conflict in conflicts[:16]
    ]
    if len(conflicts) > 16:
        safe_conflicts.append(
            {
                "field": "diagnostic_count",
                "expected": "at_most_16",
                "observed": len(conflicts),
            }
        )
    return {
        "code": code,
        "projection": safe_projection,
        "conflicts": safe_conflicts,
        "evidence_refs": [
            f"projection={safe_projection} conflict={canonical_projection_json(conflict)}"
            for conflict in safe_conflicts
        ],
        "next_safe_action": next_safe_action,
    }


def _blocked_slot(name: str, blocker: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "state": "blocked",
        "blocker_ref": f"{blocker['code']}:{name}",
        "responsible_component": name,
        "next_safe_action": blocker["next_safe_action"],
    }


_SAFE_VERSION = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._-]{0,31}"
)
_SAFE_COMPONENT = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}")
_SAFE_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_.\[\]-]{0,95}")
_TOKEN_PREFIX = re.compile(r"(?i)^(?:gh[pousr]_|bearer|token|secret|password)")


def _safe_diagnostic_value(value: Any, *, depth: int = 0) -> Any:
    """Bound and redact adapter-controlled values before durable persistence."""

    if depth > 3:
        return "<redacted-depth>"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else "<redacted-number>"
    if isinstance(value, str):
        if re.fullmatch(r"[0-9a-f]{64}", value) or _SAFE_VERSION.fullmatch(value):
            return value
        return {
            "redacted_type": "string",
            "length": len(value),
            "sha256": _bytes_sha256(value.encode("utf-8", errors="replace")),
        }
    if isinstance(value, Mapping):
        items = list(value.items())[:8]
        safe: dict[str, Any] = {}
        for key, nested in items:
            safe_key = _safe_field_name(key)
            if safe_key == "exception_type" and isinstance(nested, str):
                safe[safe_key] = _safe_component_name(nested)
            else:
                safe[safe_key] = _safe_diagnostic_value(nested, depth=depth + 1)
        if len(value) > 8:
            safe["truncated_entries"] = len(value) - 8
        return safe
    if isinstance(value, (list, tuple)):
        safe_values = [
            _safe_diagnostic_value(item, depth=depth + 1) for item in value[:8]
        ]
        if len(value) > 8:
            safe_values.append({"truncated_entries": len(value) - 8})
        return safe_values
    return {"redacted_type": type(value).__name__}


def _safe_component_name(value: Any) -> str:
    if (
        isinstance(value, str)
        and _SAFE_COMPONENT.fullmatch(value)
        and not _TOKEN_PREFIX.search(value)
    ):
        return value
    rendered = str(value).encode("utf-8", errors="replace")
    return f"redacted-{_bytes_sha256(rendered)[:12]}"


def _safe_field_name(value: Any) -> str:
    if isinstance(value, str) and _SAFE_FIELD.fullmatch(value):
        return value
    rendered = str(value).encode("utf-8", errors="replace")
    return f"redacted_field_{_bytes_sha256(rendered)[:12]}"


def _bytes_sha256(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()
