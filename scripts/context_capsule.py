#!/usr/bin/env python3
"""Immutable, CAS-protected persistence for ``context-capsule/v1``.

The head file is a convenience pointer.  Every authoritative generation is also
stored in a sibling history directory, so replacing the head never rewrites an
older generation.  Writers serialize the read/compare/write transaction with a
same-directory advisory lock and still verify the bytes after replacement.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "context-capsule/v1"
BLOCKER_VERSION = "context-capsule-blocker/v1"
BLOCKED_CAPSULE_INVALID = "BLOCKED_CAPSULE_INVALID"
BLOCKED_CAPSULE_CONFLICT = "BLOCKED_CAPSULE_CONFLICT"
BLOCKED_SOURCE_DRIFT = "BLOCKED_SOURCE_DRIFT"
BLOCKED_UNSUPPORTED_SCHEMA = "BLOCKED_UNSUPPORTED_SCHEMA"
BLOCKED_ASSURANCE_SELECTION = "BLOCKED_ASSURANCE_SELECTION"
BLOCKED_REQUIRED_ESCALATION = "BLOCKED_REQUIRED_ESCALATION"
BLOCKED_MISSING_AUTHORITY = "BLOCKED_MISSING_AUTHORITY"

BLOCKER_CODES = {
    BLOCKED_CAPSULE_INVALID,
    BLOCKED_CAPSULE_CONFLICT,
    BLOCKED_SOURCE_DRIFT,
    BLOCKED_UNSUPPORTED_SCHEMA,
    BLOCKED_ASSURANCE_SELECTION,
    BLOCKED_REQUIRED_ESCALATION,
    BLOCKED_MISSING_AUTHORITY,
}
TOP_LEVEL_FIELDS = {
    "schema_version",
    "capsule_id",
    "generation",
    "previous_digest",
    "parent_capsule",
    "objective",
    "settled_decisions",
    "source_revisions",
    "evidence_refs",
    "execution_frontier",
    "unresolved_questions",
    "next_action",
    "assurance",
    "bundle_ref",
    "privacy",
    "created_at",
    "checkpointed_at",
    "digest",
}
SEMANTIC_FIELDS = (
    "objective",
    "settled_decisions",
    "source_revisions",
    "evidence_refs",
    "execution_frontier",
    "unresolved_questions",
    "next_action",
)
MAX_OBJECTIVE_BYTES = 4096
MAX_DECISIONS = 128
MAX_SOURCES = 128
MAX_EVIDENCE_REFS = 256
MAX_QUESTIONS = 128
MAX_TEXT_BYTES = 8192
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SECRET_VALUE_RE = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9._~+/=-]{12,}|\b(?:gh[pousr]|sk)-[A-Za-z0-9_-]{12,})"
)
FORBIDDEN_PRIVACY_KEYS = {
    "transcript",
    "hidden_reasoning",
    "chain_of_thought",
    "credential",
    "credentials",
    "password",
    "secret",
    "secrets",
    "token",
    "tokens",
    "private_prompt",
    "private_prompt_text",
    "raw_payload",
    "artifact_body",
    "issue_body",
}


class CapsuleError(RuntimeError):
    """Base failure carrying the durable blocker record when one was emitted."""

    def __init__(self, message: str, blocker: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.blocker = dict(blocker or {})


class CapsuleConflict(CapsuleError):
    """A checkpoint compare-and-swap lost a race."""


class CapsuleInvalid(CapsuleError):
    """A capsule or its immutable chain is invalid."""


class SourceDrift(CapsuleError):
    """Current source identity differs from the capsule binding."""


class MissingAuthority(CapsuleError):
    """The next action does not have exact current authority."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: Mapping[str, Any], *, exclude_digest: bool = False) -> bytes:
    candidate = dict(value)
    if exclude_digest:
        candidate.pop("digest", None)
    return json.dumps(
        candidate,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def compute_digest(capsule: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(capsule, exclude_digest=True)).hexdigest()


def _timestamp(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field} must be a timezone-aware timestamp")
        return
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{field} must be a timezone-aware timestamp")
        return
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append(f"{field} must be a timezone-aware timestamp")


def _text(value: Any, field: str, errors: list[str], *, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        errors.append(f"{field} must be a non-empty string")
    elif len(value.encode("utf-8")) > MAX_TEXT_BYTES:
        errors.append(f"{field} exceeds the bounded text limit")


def _closed_object(
    value: Any,
    field: str,
    required: set[str],
    errors: list[str],
    optional: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return {}
    allowed = required | (optional or set())
    if set(value) - allowed:
        errors.append(f"{field} contains unknown fields: {sorted(set(value) - allowed)}")
    if required - set(value):
        errors.append(f"{field} is missing fields: {sorted(required - set(value))}")
    return value


def _validate_privacy(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in FORBIDDEN_PRIVACY_KEYS:
                errors.append(f"privacy minimization forbids field {path}.{key}")
            _validate_privacy(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_privacy(child, f"{path}[{index}]", errors)
    elif isinstance(value, str) and SECRET_VALUE_RE.search(value):
        errors.append(f"privacy minimization detected secret-like content at {path}")


def validate_capsule(
    capsule: Mapping[str, Any],
    *,
    previous: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return every schema, digest, privacy, and optional link error."""
    errors: list[str] = []
    if not isinstance(capsule, dict):
        return ["capsule must be an object"]
    if set(capsule) != TOP_LEVEL_FIELDS:
        missing, unknown = TOP_LEVEL_FIELDS - set(capsule), set(capsule) - TOP_LEVEL_FIELDS
        if missing:
            errors.append(f"capsule is missing fields: {sorted(missing)}")
        if unknown:
            errors.append(f"capsule contains unknown fields: {sorted(unknown)}")
    if capsule.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be exactly {SCHEMA_VERSION}")
    _text(capsule.get("capsule_id"), "capsule_id", errors)
    generation = capsule.get("generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        errors.append("generation must be an integer starting at 1")
    previous_digest = capsule.get("previous_digest")
    if previous_digest is not None and not (
        isinstance(previous_digest, str) and SHA256_RE.fullmatch(previous_digest)
    ):
        errors.append("previous_digest must be null or a lowercase SHA-256")
    parent = capsule.get("parent_capsule")
    if parent is not None:
        parent = _closed_object(parent, "parent_capsule", {"capsule_id", "generation", "digest"}, errors)
        _text(parent.get("capsule_id"), "parent_capsule.capsule_id", errors)
        if not isinstance(parent.get("generation"), int) or isinstance(parent.get("generation"), bool) or parent.get("generation", 0) < 1:
            errors.append("parent_capsule.generation must be a positive integer")
        if not isinstance(parent.get("digest"), str) or not SHA256_RE.fullmatch(parent.get("digest", "")):
            errors.append("parent_capsule.digest must be a lowercase SHA-256")
    if generation == 1 and previous_digest is not None:
        errors.append("generation 1 must have previous_digest null")
    if generation != 1 and parent is not None:
        errors.append("parent_capsule is allowed only on an explicit fork at generation 1")
    if generation != 1 and previous_digest is None:
        errors.append("generation after 1 requires previous_digest")

    _text(capsule.get("objective"), "objective", errors)
    if isinstance(capsule.get("objective"), str) and len(capsule["objective"].encode("utf-8")) > MAX_OBJECTIVE_BYTES:
        errors.append("objective exceeds the bounded objective limit")

    decisions = capsule.get("settled_decisions")
    if not isinstance(decisions, list):
        errors.append("settled_decisions must be an array")
        decisions = []
    elif len(decisions) > MAX_DECISIONS:
        errors.append("settled_decisions exceeds its bound")
    decision_ids: set[str] = set()
    for index, item in enumerate(decisions):
        item = _closed_object(item, f"settled_decisions[{index}]", {"id", "decision", "evidence_refs", "settled_at"}, errors)
        _text(item.get("id"), f"settled_decisions[{index}].id", errors)
        _text(item.get("decision"), f"settled_decisions[{index}].decision", errors)
        if item.get("id") in decision_ids:
            errors.append(f"duplicate settled decision id: {item.get('id')}")
        decision_ids.add(item.get("id"))
        if not isinstance(item.get("evidence_refs"), list) or any(not isinstance(ref, str) or not ref for ref in item.get("evidence_refs", [])):
            errors.append(f"settled_decisions[{index}].evidence_refs must be an array of IDs")
        _timestamp(item.get("settled_at"), f"settled_decisions[{index}].settled_at", errors)

    sources = capsule.get("source_revisions")
    if not isinstance(sources, list):
        errors.append("source_revisions must be an array")
        sources = []
    elif len(sources) > MAX_SOURCES:
        errors.append("source_revisions exceeds its bound")
    source_ids: set[str] = set()
    for index, item in enumerate(sources):
        item = _closed_object(item, f"source_revisions[{index}]", {"source_id", "revision", "digest"}, errors)
        for field in ("source_id", "revision"):
            _text(item.get(field), f"source_revisions[{index}].{field}", errors)
        if not isinstance(item.get("digest"), str) or not SHA256_RE.fullmatch(item.get("digest", "")):
            errors.append(f"source_revisions[{index}].digest must be a lowercase SHA-256")
        if item.get("source_id") in source_ids:
            errors.append(f"duplicate source revision id: {item.get('source_id')}")
        source_ids.add(item.get("source_id"))

    evidence = capsule.get("evidence_refs")
    if not isinstance(evidence, list):
        errors.append("evidence_refs must be an array")
        evidence = []
    elif len(evidence) > MAX_EVIDENCE_REFS:
        errors.append("evidence_refs exceeds its bound")
    evidence_ids: set[str] = set()
    for index, item in enumerate(evidence):
        item = _closed_object(item, f"evidence_refs[{index}]", {"id", "kind", "locator", "digest", "access"}, errors)
        for field in ("id", "kind", "locator", "access"):
            _text(item.get(field), f"evidence_refs[{index}].{field}", errors)
        if not isinstance(item.get("digest"), str) or not SHA256_RE.fullmatch(item.get("digest", "")):
            errors.append(f"evidence_refs[{index}].digest must be a lowercase SHA-256")
        if item.get("id") in evidence_ids:
            errors.append(f"duplicate evidence ref id: {item.get('id')}")
        evidence_ids.add(item.get("id"))
    for index, item in enumerate(decisions):
        if isinstance(item, dict):
            for ref in item.get("evidence_refs", []):
                if ref not in evidence_ids:
                    errors.append(f"settled_decisions[{index}] references unknown evidence {ref}")

    frontier = _closed_object(
        capsule.get("execution_frontier"),
        "execution_frontier",
        {"state", "next_action", "responsible_component"},
        errors,
        {"blocker_ref"},
    )
    for field in ("state", "next_action", "responsible_component"):
        _text(frontier.get(field), f"execution_frontier.{field}", errors)
    if "blocker_ref" in frontier and frontier.get("blocker_ref") is not None:
        _text(frontier.get("blocker_ref"), "execution_frontier.blocker_ref", errors)

    questions = capsule.get("unresolved_questions")
    if not isinstance(questions, list):
        errors.append("unresolved_questions must be an array")
        questions = []
    elif len(questions) > MAX_QUESTIONS:
        errors.append("unresolved_questions exceeds its bound")
    question_ids: set[str] = set()
    for index, item in enumerate(questions):
        item = _closed_object(item, f"unresolved_questions[{index}]", {"id", "question", "owner", "next_evidence_action"}, errors)
        for field in ("id", "question", "owner", "next_evidence_action"):
            _text(item.get(field), f"unresolved_questions[{index}].{field}", errors)
        if item.get("id") in question_ids:
            errors.append(f"duplicate unresolved question id: {item.get('id')}")
        question_ids.add(item.get("id"))

    next_action = _closed_object(capsule.get("next_action"), "next_action", {"description", "risk_classification", "authority_ref"}, errors)
    for field in ("description", "risk_classification", "authority_ref"):
        _text(next_action.get(field), f"next_action.{field}", errors)

    assurance = _closed_object(
        capsule.get("assurance"),
        "assurance",
        {"requested", "effective", "achieved", "selection_origin"},
        errors,
        {"legacy_subprofile"},
    )
    for field in ("requested", "effective", "achieved", "selection_origin"):
        _text(assurance.get(field), f"assurance.{field}", errors)
    if assurance.get("effective") not in {"light", "heavy"}:
        errors.append("assurance.effective must be light or heavy")
    if assurance.get("achieved") not in {"pending", "light", "heavy", "blocked"}:
        errors.append("assurance.achieved must be pending, light, heavy, or blocked")
    legacy = assurance.get("legacy_subprofile")
    if legacy not in {None, "quick", "balanced", "deep"}:
        errors.append("assurance.legacy_subprofile is unsupported")
    origin = assurance.get("selection_origin")
    if origin not in {
        "explicit_assurance",
        "legacy_phase_command",
        "legacy_inferred_command",
        "legacy_tier",
    }:
        errors.append("assurance.selection_origin is unsupported")
    if origin == "explicit_assurance" and (
        assurance.get("requested") not in {"light", "heavy"}
        or assurance.get("requested") != assurance.get("effective")
        or legacy is not None
    ):
        errors.append("explicit assurance must preserve the requested level without a legacy subprofile")
    if origin in {"legacy_phase_command", "legacy_inferred_command"} and (
        assurance.get("requested") != "heavy"
        or assurance.get("effective") != "heavy"
        or legacy is not None
    ):
        errors.append("legacy phase and inferred commands must preserve Heavy assurance")
    if origin == "legacy_tier":
        mapped = {"quick": "light", "balanced": "light", "deep": "heavy"}.get(legacy)
        if assurance.get("requested") != legacy or assurance.get("effective") != mapped:
            errors.append("legacy tier assurance mapping is invalid")
    if assurance.get("achieved") == "heavy" and assurance.get("effective") != "heavy":
        errors.append("achieved Heavy assurance requires effective Heavy assurance")

    bundle = capsule.get("bundle_ref")
    if bundle is not None:
        bundle = _closed_object(bundle, "bundle_ref", {"bundle_id", "prepared_digest"}, errors)
        _text(bundle.get("bundle_id"), "bundle_ref.bundle_id", errors)
        if not isinstance(bundle.get("prepared_digest"), str) or not SHA256_RE.fullmatch(bundle.get("prepared_digest", "")):
            errors.append("bundle_ref.prepared_digest must be a lowercase SHA-256")

    privacy = _closed_object(capsule.get("privacy"), "privacy", {"classification", "redactions", "omitted_fields", "retention_hint"}, errors)
    _text(privacy.get("classification"), "privacy.classification", errors)
    _text(privacy.get("retention_hint"), "privacy.retention_hint", errors)
    if not isinstance(privacy.get("redactions"), list):
        errors.append("privacy.redactions must be an array")
    else:
        for index, redaction in enumerate(privacy.get("redactions", [])):
            redaction = _closed_object(redaction, f"privacy.redactions[{index}]", {"source_field", "reason", "replacement_digest"}, errors)
            _text(redaction.get("source_field"), f"privacy.redactions[{index}].source_field", errors)
            _text(redaction.get("reason"), f"privacy.redactions[{index}].reason", errors)
            if not isinstance(redaction.get("replacement_digest"), str) or not SHA256_RE.fullmatch(redaction.get("replacement_digest", "")):
                errors.append(f"privacy.redactions[{index}].replacement_digest must be a lowercase SHA-256")
    if not isinstance(privacy.get("omitted_fields"), list) or any(not isinstance(item, str) or not item for item in privacy.get("omitted_fields", [])):
        errors.append("privacy.omitted_fields must be an array of field names")
    else:
        required_omissions = {"transcript", "hidden_reasoning", "credentials"}
        if not required_omissions <= set(privacy["omitted_fields"]):
            errors.append("privacy.omitted_fields must record transcript, hidden_reasoning, and credentials")

    _timestamp(capsule.get("created_at"), "created_at", errors)
    _timestamp(capsule.get("checkpointed_at"), "checkpointed_at", errors)
    digest = capsule.get("digest")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        errors.append("digest must be a lowercase SHA-256")
    else:
        try:
            expected = compute_digest(capsule)
        except (TypeError, ValueError, OverflowError):
            errors.append("capsule is not canonical-JSON serializable")
        else:
            if digest != expected:
                errors.append("digest does not match canonical capsule bytes")

    if previous is not None:
        previous_errors = validate_capsule(previous)
        if previous_errors:
            errors.append("previous capsule is invalid")
        elif capsule.get("capsule_id") != previous.get("capsule_id"):
            errors.append("capsule_id changed within a lineage")
        elif capsule.get("generation") != previous.get("generation", 0) + 1:
            errors.append("generation must increment by exactly one")
        elif capsule.get("previous_digest") != previous.get("digest"):
            errors.append("previous_digest does not bind the prior generation")
        elif capsule.get("created_at") != previous.get("created_at"):
            errors.append("created_at changed within a lineage")

    _validate_privacy(capsule, "capsule", errors)
    return errors


def _history_dir(path: Path) -> Path:
    return path.parent / f".{path.name}.generations"


def _blocker_dir(path: Path) -> Path:
    return path.parent / f".{path.name}.blockers"


def _generation_path(path: Path, generation: int, digest: str) -> Path:
    return _history_dir(path) / f"{generation:08d}-{digest}.json"


def _atomic_write(path: Path, payload: bytes, *, replace: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if not replace and path.exists():
            raise FileExistsError(path)
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


@contextmanager
def _writer_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / f".{path.name}.lock"
    with lock_path.open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise CapsuleInvalid(f"capsule bytes are unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise CapsuleInvalid("capsule must be a JSON object")
    return value


def read_head(path: str | Path) -> dict[str, Any]:
    return _read_json(Path(path))


def _blocker(
    code: str,
    *,
    evidence_refs: Iterable[str] = (),
    responsible_component: str = "context-capsule",
    execution_frontier: Mapping[str, Any] | None = None,
    next_safe_action: str,
) -> dict[str, Any]:
    if code not in BLOCKER_CODES:
        code = BLOCKED_UNSUPPORTED_SCHEMA
    return {
        "blocker_version": BLOCKER_VERSION,
        "code": code,
        "evidence_refs": sorted(set(evidence_refs)),
        "responsible_component": responsible_component,
        "execution_frontier": copy.deepcopy(dict(execution_frontier or {})),
        "next_safe_action": next_safe_action,
        "recorded_at": utc_now(),
    }


def persist_blocker(path: str | Path, blocker: Mapping[str, Any]) -> Path:
    path = Path(path)
    target = _blocker_dir(path) / f"{blocker.get('recorded_at', utc_now()).replace(':', '')}-{uuid.uuid4().hex}.json"
    _atomic_write(target, canonical_bytes(blocker) + b"\n", replace=False)
    return target


def list_blockers(path: str | Path) -> list[dict[str, Any]]:
    directory = _blocker_dir(Path(path))
    if not directory.is_dir():
        return []
    records = []
    for candidate in sorted(directory.glob("*.json")):
        try:
            record = _read_json(candidate)
        except CapsuleInvalid:
            records.append(_blocker(BLOCKED_CAPSULE_INVALID, next_safe_action="Restore the blocker ledger from verified storage or stop."))
        else:
            records.append(record)
    return records


def _raise_with_blocker(path: Path, exc_type: type[CapsuleError], message: str, blocker: Mapping[str, Any]) -> None:
    persist_blocker(path, blocker)
    raise exc_type(message, blocker)


def _write_generation(path: Path, capsule: Mapping[str, Any]) -> None:
    payload = canonical_bytes(capsule) + b"\n"
    generation_path = _generation_path(
        path, int(capsule["generation"]), str(capsule["digest"])
    )
    if generation_path.exists():
        existing = _read_json(generation_path)
        if existing.get("digest") != capsule.get("digest"):
            raise CapsuleConflict("immutable generation already exists with different bytes")
    else:
        _atomic_write(generation_path, payload, replace=False)
    _atomic_write(path, payload)
    reread = _read_json(path)
    if reread.get("digest") != capsule.get("digest") or canonical_bytes(reread) != canonical_bytes(capsule):
        raise CapsuleInvalid("atomic replacement did not preserve the new capsule bytes")


def create(
    path: str | Path,
    *,
    capsule_id: str,
    objective: str,
    settled_decisions: list[dict[str, Any]],
    source_revisions: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    execution_frontier: dict[str, Any],
    unresolved_questions: list[dict[str, Any]],
    next_action: dict[str, Any],
    assurance: dict[str, Any],
    bundle_ref: dict[str, Any] | None = None,
    privacy: dict[str, Any] | None = None,
    parent_capsule: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    path = Path(path)
    timestamp = timestamp or utc_now()
    capsule = {
        "schema_version": SCHEMA_VERSION,
        "capsule_id": capsule_id,
        "generation": 1,
        "previous_digest": None,
        "parent_capsule": copy.deepcopy(parent_capsule),
        "objective": objective,
        "settled_decisions": copy.deepcopy(settled_decisions),
        "source_revisions": copy.deepcopy(source_revisions),
        "evidence_refs": copy.deepcopy(evidence_refs),
        "execution_frontier": copy.deepcopy(execution_frontier),
        "unresolved_questions": copy.deepcopy(unresolved_questions),
        "next_action": copy.deepcopy(next_action),
        "assurance": copy.deepcopy(assurance),
        "bundle_ref": copy.deepcopy(bundle_ref),
        "privacy": copy.deepcopy(privacy or {
            "classification": "repository_metadata",
            "redactions": [],
            "omitted_fields": ["transcript", "hidden_reasoning", "credentials"],
            "retention_hint": "follow repository policy",
        }),
        "created_at": timestamp,
        "checkpointed_at": timestamp,
        "digest": "",
    }
    capsule["digest"] = compute_digest(capsule)
    errors = validate_capsule(capsule)
    if errors:
        blocker = _blocker(BLOCKED_CAPSULE_INVALID, execution_frontier=execution_frontier, next_safe_action="Correct the capsule input without copying private payloads, then create a new lineage.")
        _raise_with_blocker(path, CapsuleInvalid, "; ".join(errors), blocker)
    with _writer_lock(path):
        if path.exists():
            blocker = _blocker(BLOCKED_CAPSULE_CONFLICT, execution_frontier=execution_frontier, next_safe_action="Read the existing lineage or choose a new capsule path and ID.")
            _raise_with_blocker(path, CapsuleConflict, "capsule lineage already exists", blocker)
        _write_generation(path, capsule)
    return capsule


def verify_chain(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        head = _read_json(path)
    except CapsuleInvalid as exc:
        blocker = _blocker(BLOCKED_CAPSULE_INVALID, next_safe_action="Restore a verified generation or stop.")
        _raise_with_blocker(path, CapsuleInvalid, str(exc), blocker)
    if head.get("schema_version") != SCHEMA_VERSION:
        blocker = _blocker(BLOCKED_UNSUPPORTED_SCHEMA, execution_frontier=head.get("execution_frontier", {}), next_safe_action="Use a compatible read-only reader or an explicit migration.")
        _raise_with_blocker(path, CapsuleInvalid, "unsupported capsule schema", blocker)
    generation = head.get("generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        blocker = _blocker(BLOCKED_CAPSULE_INVALID, execution_frontier=head.get("execution_frontier", {}), next_safe_action="Restore a verified generation or stop.")
        _raise_with_blocker(path, CapsuleInvalid, "invalid head generation", blocker)
    reverse_chain = [head]
    current = head
    for number in range(generation, 0, -1):
        generation_path = _generation_path(path, number, str(current.get("digest", "")))
        try:
            stored = _read_json(generation_path)
        except CapsuleInvalid as exc:
            blocker = _blocker(BLOCKED_CAPSULE_INVALID, execution_frontier=head.get("execution_frontier", {}), next_safe_action="Restore the missing or corrupt verified generation, or stop.")
            _raise_with_blocker(path, CapsuleInvalid, f"generation {number}: {exc}", blocker)
        if canonical_bytes(stored) != canonical_bytes(current):
            blocker = _blocker(BLOCKED_CAPSULE_INVALID, execution_frontier=head.get("execution_frontier", {}), next_safe_action="Restore a verified generation or stop.")
            _raise_with_blocker(path, CapsuleInvalid, f"generation {number} does not match its content-addressed bytes", blocker)
        if number > 1:
            previous_path = _generation_path(
                path, number - 1, str(current.get("previous_digest", ""))
            )
            try:
                current = _read_json(previous_path)
            except CapsuleInvalid as exc:
                blocker = _blocker(BLOCKED_CAPSULE_INVALID, execution_frontier=head.get("execution_frontier", {}), next_safe_action="Restore the missing or corrupt verified generation, or stop.")
                _raise_with_blocker(path, CapsuleInvalid, f"generation {number - 1}: {exc}", blocker)
            reverse_chain.append(current)
    previous = None
    for capsule in reversed(reverse_chain):
        errors = validate_capsule(capsule, previous=previous)
        if errors:
            blocker = _blocker(BLOCKED_CAPSULE_INVALID, execution_frontier=head.get("execution_frontier", {}), next_safe_action="Restore a verified generation or stop.")
            _raise_with_blocker(path, CapsuleInvalid, f"generation {capsule.get('generation')}: {'; '.join(errors)}", blocker)
        previous = capsule
    if canonical_bytes(reverse_chain[0]) != canonical_bytes(head):
        blocker = _blocker(BLOCKED_CAPSULE_INVALID, execution_frontier=head.get("execution_frontier", {}), next_safe_action="Restore the head from its verified immutable generation or stop.")
        _raise_with_blocker(path, CapsuleInvalid, "head does not match its immutable generation", blocker)
    return head


def checkpoint(
    path: str | Path,
    *,
    expected_generation: int,
    expected_digest: str,
    changes: Mapping[str, Any],
    timestamp: str | None = None,
) -> dict[str, Any]:
    path = Path(path)
    allowed = set(SEMANTIC_FIELDS) | {"assurance", "bundle_ref", "privacy"}
    if set(changes) - allowed:
        blocker = _blocker(BLOCKED_CAPSULE_INVALID, next_safe_action="Checkpoint only mutable semantic, assurance, bundle, or privacy fields.")
        _raise_with_blocker(path, CapsuleInvalid, f"checkpoint contains immutable or unknown fields: {sorted(set(changes) - allowed)}", blocker)
    with _writer_lock(path):
        current = verify_chain(path)
        if current["generation"] != expected_generation or current["digest"] != expected_digest:
            blocker = _blocker(
                BLOCKED_CAPSULE_CONFLICT,
                evidence_refs=[current["digest"]],
                execution_frontier=current["execution_frontier"],
                next_safe_action="Re-read, reconcile semantic changes, and checkpoint against the new head.",
            )
            _raise_with_blocker(path, CapsuleConflict, "checkpoint compare-and-swap conflict", blocker)
        if current["execution_frontier"].get("state") == "archived":
            blocker = _blocker(BLOCKED_CAPSULE_CONFLICT, evidence_refs=[current["digest"]], execution_frontier=current["execution_frontier"], next_safe_action="Fork the archived lineage explicitly before continuing.")
            _raise_with_blocker(path, CapsuleConflict, "archived lineage is immutable", blocker)
        candidate = copy.deepcopy(current)
        candidate.update(copy.deepcopy(dict(changes)))
        candidate["generation"] = current["generation"] + 1
        candidate["previous_digest"] = current["digest"]
        candidate["parent_capsule"] = None
        candidate["checkpointed_at"] = timestamp or utc_now()
        candidate["digest"] = compute_digest(candidate)
        errors = validate_capsule(candidate, previous=current)
        if errors:
            blocker = _blocker(BLOCKED_CAPSULE_INVALID, execution_frontier=current["execution_frontier"], next_safe_action="Correct the checkpoint delta and retry against the same verified head.")
            _raise_with_blocker(path, CapsuleInvalid, "; ".join(errors), blocker)
        _write_generation(path, candidate)
        return candidate


def _source_identity(value: Any) -> tuple[str, str] | None:
    if isinstance(value, dict):
        revision, digest = value.get("revision"), value.get("digest")
    elif isinstance(value, (tuple, list)) and len(value) == 2:
        revision, digest = value
    else:
        return None
    if not isinstance(revision, str) or not isinstance(digest, str):
        return None
    return revision, digest


def resume(
    path: str | Path,
    *,
    current_source_revisions: Mapping[str, Any],
    authority_refs: Iterable[str] = (),
    checkpoint_blocked: bool = True,
) -> dict[str, Any]:
    path = Path(path)
    capsule = verify_chain(path)
    if capsule["execution_frontier"].get("state") == "archived":
        blocker = _blocker(BLOCKED_CAPSULE_CONFLICT, evidence_refs=[capsule["digest"]], execution_frontier=capsule["execution_frontier"], next_safe_action="Fork the archived lineage explicitly before continuing.")
        _raise_with_blocker(path, CapsuleConflict, "archived lineage cannot resume", blocker)
    drift = []
    for source in capsule["source_revisions"]:
        current = _source_identity(current_source_revisions.get(source["source_id"]))
        if current != (source["revision"], source["digest"]):
            drift.append(source["source_id"])
    if drift:
        blocker = _blocker(BLOCKED_SOURCE_DRIFT, evidence_refs=drift, execution_frontier=capsule["execution_frontier"], next_safe_action="Reconcile against current authority before continuing.")
        persist_blocker(path, blocker)
        if checkpoint_blocked:
            blocked_frontier = copy.deepcopy(capsule["execution_frontier"])
            blocked_frontier.update({"state": "blocked", "next_action": blocker["next_safe_action"], "responsible_component": "context-capsule", "blocker_ref": BLOCKED_SOURCE_DRIFT})
            blocked_assurance = copy.deepcopy(capsule["assurance"])
            blocked_assurance["achieved"] = "blocked"
            try:
                checkpoint(path, expected_generation=capsule["generation"], expected_digest=capsule["digest"], changes={"execution_frontier": blocked_frontier, "assurance": blocked_assurance})
            except CapsuleError:
                pass
        raise SourceDrift(f"source revision drift: {', '.join(drift)}", blocker)
    known_authority = {item["source_id"] for item in capsule["source_revisions"]} | {item["id"] for item in capsule["evidence_refs"]} | set(authority_refs)
    required_authority = capsule["next_action"]["authority_ref"]
    if required_authority not in known_authority:
        blocker = _blocker(BLOCKED_MISSING_AUTHORITY, evidence_refs=[required_authority], execution_frontier=capsule["execution_frontier"], next_safe_action="Acquire exact authority or stop.")
        persist_blocker(path, blocker)
        if checkpoint_blocked:
            blocked_frontier = copy.deepcopy(capsule["execution_frontier"])
            blocked_frontier.update({"state": "blocked", "next_action": blocker["next_safe_action"], "responsible_component": "context-capsule", "blocker_ref": BLOCKED_MISSING_AUTHORITY})
            blocked_assurance = copy.deepcopy(capsule["assurance"])
            blocked_assurance["achieved"] = "blocked"
            try:
                checkpoint(path, expected_generation=capsule["generation"], expected_digest=capsule["digest"], changes={"execution_frontier": blocked_frontier, "assurance": blocked_assurance})
            except CapsuleError:
                pass
        raise MissingAuthority("next action lacks matching authority", blocker)
    return capsule


def fork(
    parent_path: str | Path,
    target_path: str | Path,
    *,
    capsule_id: str,
    changes: Mapping[str, Any] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    parent = verify_chain(parent_path)
    values = {field: copy.deepcopy(parent[field]) for field in SEMANTIC_FIELDS}
    values.update({"assurance": copy.deepcopy(parent["assurance"]), "bundle_ref": copy.deepcopy(parent["bundle_ref"]), "privacy": copy.deepcopy(parent["privacy"])})
    if changes:
        unknown = set(changes) - set(values)
        if unknown:
            raise CapsuleInvalid(f"fork contains unknown fields: {sorted(unknown)}")
        values.update(copy.deepcopy(dict(changes)))
    return create(
        target_path,
        capsule_id=capsule_id,
        parent_capsule={"capsule_id": parent["capsule_id"], "generation": parent["generation"], "digest": parent["digest"]},
        timestamp=timestamp,
        **values,
    )


def compact(
    path: str | Path,
    *,
    expected_generation: int,
    expected_digest: str,
    settled_decisions: list[dict[str, Any]] | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    unresolved_questions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    current = verify_chain(path)
    proposed_decisions = copy.deepcopy(settled_decisions if settled_decisions is not None else current["settled_decisions"])
    proposed_evidence = copy.deepcopy(evidence_refs if evidence_refs is not None else current["evidence_refs"])
    proposed_questions = copy.deepcopy(unresolved_questions if unresolved_questions is not None else current["unresolved_questions"])
    current_questions = {item["id"] for item in current["unresolved_questions"]}
    if not current_questions <= {item.get("id") for item in proposed_questions if isinstance(item, dict)}:
        raise CapsuleInvalid("compaction cannot drop unresolved questions")
    if proposed_questions != current["unresolved_questions"]:
        raise CapsuleInvalid("compaction cannot rewrite active unresolved questions")
    current_decisions = {item["id"]: set(item["evidence_refs"]) for item in current["settled_decisions"]}
    proposed_decision_map = {item.get("id"): set(item.get("evidence_refs", [])) for item in proposed_decisions if isinstance(item, dict)}
    for decision_id, refs in current_decisions.items():
        if decision_id not in proposed_decision_map or not refs <= proposed_decision_map[decision_id]:
            raise CapsuleInvalid("compaction cannot drop settled decisions or their evidence provenance")
    if proposed_decisions != current["settled_decisions"]:
        raise CapsuleInvalid("compaction cannot rewrite settled decisions")
    required_evidence = set().union(*current_decisions.values()) if current_decisions else set()
    proposed_ids = {item.get("id") for item in proposed_evidence if isinstance(item, dict)}
    blocker_ref = current["execution_frontier"].get("blocker_ref")
    if blocker_ref in {item["id"] for item in current["evidence_refs"]}:
        required_evidence.add(blocker_ref)
    if not required_evidence <= proposed_ids:
        raise CapsuleInvalid("compaction cannot drop evidence needed by settled decisions")
    current_evidence = {item["id"]: item for item in current["evidence_refs"]}
    proposed_evidence_map = {
        item.get("id"): item for item in proposed_evidence if isinstance(item, dict)
    }
    if any(
        proposed_evidence_map.get(evidence_id) != current_evidence[evidence_id]
        for evidence_id in required_evidence
    ):
        raise CapsuleInvalid("compaction cannot rewrite required evidence provenance")
    return checkpoint(
        path,
        expected_generation=expected_generation,
        expected_digest=expected_digest,
        changes={"settled_decisions": proposed_decisions, "evidence_refs": proposed_evidence, "unresolved_questions": proposed_questions},
    )


def supersede(
    path: str | Path,
    *,
    expected_generation: int,
    expected_digest: str,
    successor: Mapping[str, Any],
) -> dict[str, Any]:
    pointer = _closed_object(dict(successor), "successor", {"capsule_id", "generation", "digest"}, [])
    if not pointer or not SHA256_RE.fullmatch(str(pointer.get("digest", ""))):
        raise CapsuleInvalid("successor must identify capsule_id, generation, and digest")
    frontier = {
        "state": "superseded",
        "next_action": f"Resume successor capsule {pointer['capsule_id']}",
        "responsible_component": "context-capsule",
        "blocker_ref": None,
    }
    action = {
        "description": f"Resume successor capsule {pointer['capsule_id']} generation {pointer['generation']}",
        "risk_classification": "ordinary_scoped_recoverable",
        "authority_ref": str(pointer["capsule_id"]),
    }
    evidence = copy.deepcopy(verify_chain(path)["evidence_refs"])
    evidence.append({"id": str(pointer["capsule_id"]), "kind": "successor_capsule", "locator": f"context-capsule:{pointer['capsule_id']}:{pointer['generation']}", "digest": pointer["digest"], "access": "same-policy"})
    return checkpoint(path, expected_generation=expected_generation, expected_digest=expected_digest, changes={"execution_frontier": frontier, "next_action": action, "evidence_refs": evidence})


def archive(path: str | Path, *, expected_generation: int, expected_digest: str) -> dict[str, Any]:
    current = verify_chain(path)
    frontier = {
        "state": "archived",
        "next_action": "Fork this archived lineage explicitly before resuming",
        "responsible_component": "context-capsule",
        "blocker_ref": None,
    }
    action = {
        "description": "Fork this archived lineage explicitly before resuming",
        "risk_classification": "ordinary_scoped_recoverable",
        "authority_ref": current["next_action"]["authority_ref"],
    }
    return checkpoint(path, expected_generation=expected_generation, expected_digest=expected_digest, changes={"execution_frontier": frontier, "next_action": action})


def status(path: str | Path, expected_ref: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        capsule = verify_chain(path)
    except CapsuleError as exc:
        return {"status": "INVALID", "error": str(exc), "blocker": exc.blocker, "durable_blockers": list_blockers(path)}
    errors = []
    if expected_ref:
        for field in ("schema_version", "capsule_id", "generation", "digest"):
            if expected_ref.get(field) != capsule.get(field):
                errors.append(f"manifest context_capsule_ref.{field} does not match verified head")
    return {
        "status": "VALID" if not errors else "INVALID",
        "errors": errors,
        "ref": {field: capsule[field] for field in ("schema_version", "capsule_id", "generation", "digest")},
        "execution_frontier": capsule["execution_frontier"],
        "assurance": capsule["assurance"],
        "durable_blockers": list_blockers(path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("capsule", type=Path)
    resume_parser = subparsers.add_parser("resume")
    resume_parser.add_argument("capsule", type=Path)
    resume_parser.add_argument("--source-revisions", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            result = status(args.capsule)
        else:
            sources = json.loads(args.source_revisions.read_text(encoding="utf-8"))
            capsule = resume(args.capsule, current_source_revisions=sources)
            result = {"status": "RESUMABLE", "ref": {field: capsule[field] for field in ("schema_version", "capsule_id", "generation", "digest")}, "assurance": capsule["assurance"]}
    except (CapsuleError, OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "BLOCKED", "error": str(exc), "blocker": getattr(exc, "blocker", {})}
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in {"VALID", "RESUMABLE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
