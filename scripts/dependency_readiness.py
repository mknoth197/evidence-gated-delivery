#!/usr/bin/env python3
"""Validate structured external task gates before Plan exit or implementation."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from plan_tasks import issue_body_sha256, parse_tasks


POLICY_VERSION = "dependency-readiness/v1"
READINESS_STATUSES = {"READY", "PARTIAL_ONLY", "BLOCKED"}
LEGACY_GATE_PATTERN = re.compile(
    r"\b(?:hard\s+)?entry[- ]gate\b|\bmay begin only after\b|"
    r"\bcannot start until\b|\brequires?\b.{0,100}\bbefore (?:work|implementation) starts\b|"
    r"\bdepends on\b.{0,100}\b(?:upstream|issue\s+#?\d+)\b",
    re.IGNORECASE,
)
SHA256 = re.compile(r"[0-9a-f]{64}")

AuthorityReader = Callable[[str], tuple[dict[str, Any] | None, str | None]]
InterfaceReader = Callable[[str, str, str], tuple[bytes | None, str | None]]
PhaseReceiptVerifier = Callable[[dict[str, Any], str], list[str]]
AuthorizationVerifier = Callable[[dict[str, Any], dict[str, Any], list[str]], list[str]]
_PHASE_RECEIPT_REPLAY_STACK: set[tuple[str, str]] = set()


def canonical_phase_receipt_replay(
    manifest: dict[str, Any], phase: str, validator: Callable[[dict[str, Any], str, bool], list[str]]
) -> list[str]:
    """Replay the canonical validator and reject cyclic receipt trust chains."""

    key = (str(manifest.get("run_id", "")), phase)
    if key in _PHASE_RECEIPT_REPLAY_STACK:
        return ["cyclic phase-receipt replay detected"]
    _PHASE_RECEIPT_REPLAY_STACK.add(key)
    try:
        return validator(manifest, phase, False)
    finally:
        _PHASE_RECEIPT_REPLAY_STACK.remove(key)


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def legacy_semantic_entry_gates(tasks: list[dict[str, Any]]) -> list[str]:
    """Migration guard only: legacy prose can block but can never grant readiness."""

    return [
        task["task_id"]
        for task in tasks
        if not task.get("entry_gates")
        and LEGACY_GATE_PATTERN.search(
            " ".join(
                str(task.get(field, ""))
                for field in ("context", "requirements", "complete_when")
            )
        )
    ]


def transitive_deferred_tasks(
    tasks: list[dict[str, Any]], blocked_task_ids: set[str]
) -> list[str]:
    deferred = set(blocked_task_ids)
    changed = True
    while changed:
        changed = False
        for task in tasks:
            if task["task_id"] not in deferred and any(
                dependency in deferred for dependency in task["depends_on"]
            ):
                deferred.add(task["task_id"])
                changed = True
    return sorted(deferred)


def _validate_dependency_classification(
    data: dict[str, Any], body: str, tasks: list[dict[str, Any]]
) -> list[str]:
    """Bind every gated/ungated task decision to the independent Plan audit."""

    evidence = data.get("dependency_classification_evidence")
    if not isinstance(evidence, dict):
        return ["structured entry gates require dependency classification evidence"]
    expected = [
        {
            "task_id": task["task_id"],
            "disposition": "gated" if task["entry_gates"] else "none",
            "gate_ids": [gate["gate_id"] for gate in task["entry_gates"]],
        }
        for task in tasks
    ]
    errors: list[str] = []
    if evidence.get("policy_version") != "dependency-classification/v1":
        errors.append(
            "dependency_classification_evidence.policy_version must equal dependency-classification/v1"
        )
    if evidence.get("authoritative_issue_body_sha256") != issue_body_sha256(body):
        errors.append("dependency classification does not bind authoritative Plan bytes")
    if evidence.get("classifications") != expected:
        errors.append("dependency classifications must exactly cover every task and gate")
    body_sha = issue_body_sha256(body)
    digest = hashlib.sha256(
        json.dumps(
            {"authoritative_issue_body_sha256": body_sha, "classifications": expected},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    marker = f"DEPENDENCY-CLASSIFICATION:{digest}:PASS"
    if evidence.get("audit_marker") != marker:
        errors.append("dependency classification audit marker does not bind classifications")
    audit_agent_id = evidence.get("audit_agent_id")
    audits = data.get("plan_audits")
    final_audit = audits[-1] if isinstance(audits, list) and audits else None
    bound = (
        isinstance(final_audit, dict)
        and final_audit.get("kind") == "final_remote"
        and final_audit.get("agent_id") == audit_agent_id
        and final_audit.get("status") == "completed"
        and final_audit.get("receipt_kind") == "collaboration_delegated"
        and final_audit.get("reviewed_body_sha256") == body_sha
        and final_audit.get("callback_sha256") == evidence.get("audit_callback_sha256")
        and marker in final_audit.get("evidence_ids", [])
    )
    if not bound:
        errors.append("dependency classification lacks a completed independent Plan audit binding")
    return errors


def _read_bound_json(
    path_value: Any, expected_sha: Any, label: str, errors: list[str]
) -> dict[str, Any] | None:
    if not isinstance(path_value, str) or not Path(path_value).is_absolute():
        errors.append(f"{label} must be an absolute readable path")
        return None
    path = Path(path_value)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        errors.append(f"{label} is unreadable: {exc}")
        return None
    actual_sha = hashlib.sha256(raw).hexdigest()
    if expected_sha != actual_sha:
        errors.append(f"{label} SHA-256 does not match current bytes")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        errors.append(f"{label} must contain valid JSON")
        return None
    if not isinstance(value, dict):
        errors.append(f"{label} must contain a JSON object")
        return None
    return value


def _validate_phase_receipt(
    predicate: str,
    evidence: dict[str, Any],
    authority_url: str,
    label: str,
    errors: list[str],
    phase_receipt_verifier: PhaseReceiptVerifier | None,
) -> None:
    _prefix, phase, expected_status = predicate.split(":", 2)
    receipt = _read_bound_json(
        evidence.get("receipt_path"), evidence.get("receipt_sha256"),
        f"{label}.receipt_path", errors,
    )
    manifest = _read_bound_json(
        evidence.get("manifest_path"), evidence.get("manifest_sha256"),
        f"{label}.manifest_path", errors,
    )
    if receipt is None or manifest is None:
        return
    if receipt.get("phase") != phase or receipt.get("status") != expected_status:
        errors.append(f"{label} does not prove {predicate}")
    if receipt.get("remote_verification") is not True or receipt.get("errors") != []:
        errors.append(f"{label} is not a clean remote-verification receipt")
    if not _timestamp(receipt.get("validated_at")):
        errors.append(f"{label} receipt validated_at is invalid")
    if receipt.get("receipt_path") != evidence.get("receipt_path"):
        errors.append(f"{label} receipt does not bind its own path")
    if receipt.get("manifest") != evidence.get("manifest_path"):
        errors.append(f"{label} receipt does not bind its manifest path")
    if receipt.get("manifest_sha256") != evidence.get("manifest_sha256"):
        errors.append(f"{label} receipt does not bind its manifest SHA-256")
    if manifest.get("implementation_issue_url") != authority_url:
        errors.append(f"{label} manifest does not bind the declared gate authority")
    if phase_receipt_verifier is None:
        errors.append(f"{label} requires independent validator replay")
    else:
        replay_errors = phase_receipt_verifier(manifest, phase)
        if replay_errors:
            errors.append(f"{label} validator replay failed: {'; '.join(replay_errors)}")


def _validate_merged_interface(
    predicate: str,
    evidence: dict[str, Any],
    label: str,
    errors: list[str],
    interface_reader: InterfaceReader | None,
) -> None:
    version = predicate.split(":", 1)[1]
    for field in ("repository", "commit_sha", "interface_path", "blob_sha256"):
        if not isinstance(evidence.get(field), str) or not evidence[field]:
            errors.append(f"{label}.{field} is required")
    if not re.fullmatch(r"[0-9a-f]{40}", str(evidence.get("commit_sha", ""))):
        errors.append(f"{label}.commit_sha must be a full lowercase commit SHA")
    if not SHA256.fullmatch(str(evidence.get("blob_sha256", ""))):
        errors.append(f"{label}.blob_sha256 must be a lowercase SHA-256")
    if interface_reader is None:
        errors.append(f"{label} requires live merged-interface verification")
        return
    raw, read_error = interface_reader(
        str(evidence.get("repository", "")),
        str(evidence.get("commit_sha", "")),
        str(evidence.get("interface_path", "")),
    )
    if read_error or raw is None:
        errors.append(f"{label} merged interface read-back failed: {read_error or 'missing bytes'}")
        return
    if hashlib.sha256(raw).hexdigest() != evidence.get("blob_sha256"):
        errors.append(f"{label}.blob_sha256 does not match merged interface bytes")
    if version.encode("utf-8") not in raw:
        errors.append(f"{label} merged interface bytes do not contain {version}")


def validate_dependency_readiness_evidence(
    data: dict[str, Any],
    body: str,
    tasks: list[dict[str, Any]] | None = None,
    *,
    require_structured: bool = False,
    authority_reader: AuthorityReader | None = None,
    interface_reader: InterfaceReader | None = None,
    phase_receipt_verifier: PhaseReceiptVerifier | None = None,
    authorization_verifier: AuthorizationVerifier | None = None,
) -> list[str]:
    parsed = tasks if tasks is not None else parse_tasks(
        body, require_entry_gates=require_structured
    )
    if require_structured:
        classification_errors = _validate_dependency_classification(data, body, parsed)
        if classification_errors:
            return classification_errors
    legacy = legacy_semantic_entry_gates(parsed)
    if legacy:
        return [
            "semantic external prerequisites require Plan repair with typed entry_gates: "
            + ", ".join(legacy)
        ]
    expected = [
        {"task_id": task["task_id"], **gate}
        for task in parsed
        for gate in task["entry_gates"]
    ]
    if not expected:
        return []
    receipt = data.get("dependency_readiness_evidence")
    if not isinstance(receipt, dict) or not receipt:
        return ["dependency readiness evidence is required for structured entry gates"]

    errors: list[str] = []
    if receipt.get("policy_version") != POLICY_VERSION:
        errors.append(f"dependency_readiness_evidence.policy_version must equal {POLICY_VERSION}")
    if receipt.get("authoritative_issue_body_sha256") != issue_body_sha256(body):
        errors.append("dependency readiness evidence does not bind authoritative Plan bytes")
    status = receipt.get("status")
    if status not in READINESS_STATUSES:
        errors.append("dependency readiness status must be READY, PARTIAL_ONLY, or BLOCKED")
    entries = receipt.get("gates")
    if not isinstance(entries, list):
        errors.append("dependency_readiness_evidence.gates must be an array")
        entries = []
    expected_by_id = {gate["gate_id"]: gate for gate in expected}
    observed_by_id: dict[str, dict[str, Any]] = {}
    blocked_roots: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"dependency_readiness_evidence.gates[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        gate_id = entry.get("gate_id")
        declared = expected_by_id.get(str(gate_id))
        if declared is None:
            errors.append(f"{label}.gate_id is not declared by the Plan")
            continue
        if gate_id in observed_by_id:
            errors.append(f"duplicate dependency readiness gate {gate_id}")
        observed_by_id[str(gate_id)] = entry
        for field in ("task_id", "authority_url"):
            if entry.get(field) != declared[field]:
                errors.append(f"{label}.{field} does not match the structured Plan gate")
        if authority_reader is None:
            errors.append(f"{label} requires live authority verification")
        else:
            authority, read_error = authority_reader(declared["authority_url"])
            if read_error or authority is None:
                errors.append(f"{label} authority read-back failed: {read_error or 'missing authority'}")
            else:
                if entry.get("authority_state") != authority.get("state"):
                    errors.append(f"{label}.authority_state does not match live authority")
                body_value = authority.get("body")
                actual_body_sha = (
                    hashlib.sha256(body_value.encode("utf-8")).hexdigest()
                    if isinstance(body_value, str) else None
                )
                if entry.get("authority_body_sha256") != actual_body_sha:
                    errors.append(f"{label}.authority_body_sha256 does not match live authority bytes")
        if not _timestamp(entry.get("verified_at")):
            errors.append(f"{label}.verified_at must be a timezone-aware timestamp")
        predicate_entries = entry.get("predicates")
        if not isinstance(predicate_entries, list):
            errors.append(f"{label}.predicates must be an array")
            predicate_entries = []
        observed_predicates = [
            item.get("predicate")
            for item in predicate_entries
            if isinstance(item, dict) and isinstance(item.get("predicate"), str)
        ]
        if len(observed_predicates) != len(predicate_entries):
            errors.append(f"{label}.predicates entries must be typed evidence objects")
        if len(observed_predicates) != len(set(observed_predicates)):
            errors.append(f"{label}.predicates contains duplicate predicate evidence")
        by_predicate = {
            item["predicate"]: item
            for item in predicate_entries
            if isinstance(item, dict) and isinstance(item.get("predicate"), str)
        }
        if sorted(by_predicate) != sorted(declared["predicates"]):
            errors.append(f"{label}.predicates do not exactly cover the structured Plan predicates")
        for predicate in declared["predicates"]:
            evidence = by_predicate.get(predicate)
            predicate_label = f"{label}.{predicate}"
            if not isinstance(evidence, dict):
                continue
            state = evidence.get("state")
            if state == "blocked":
                blocked_roots.add(declared["task_id"])
                if not isinstance(evidence.get("reason"), str) or not evidence["reason"].strip():
                    errors.append(f"{predicate_label}.reason is required when blocked")
                continue
            if state != "verified":
                errors.append(f"{predicate_label}.state must be verified or blocked")
                continue
            if predicate.startswith("phase_receipt:"):
                _validate_phase_receipt(
                    predicate,
                    evidence,
                    declared["authority_url"],
                    predicate_label,
                    errors,
                    phase_receipt_verifier,
                )
            elif predicate.startswith("merged_interface:"):
                _validate_merged_interface(
                    predicate, evidence, predicate_label, errors, interface_reader
                )
    if sorted(observed_by_id) != sorted(expected_by_id):
        errors.append("dependency readiness gates must cover every structured Plan gate exactly once")

    deferred = transitive_deferred_tasks(parsed, blocked_roots)
    all_ids = [task["task_id"] for task in parsed]
    executable = [task_id for task_id in all_ids if task_id not in deferred]
    if status == "READY":
        if blocked_roots:
            errors.append("READY dependency evidence cannot contain blocked predicates")
        if receipt.get("deferred_task_ids") not in ([], None):
            errors.append("READY dependency evidence must not declare deferred tasks")
    elif status == "PARTIAL_ONLY":
        if not blocked_roots:
            errors.append("PARTIAL_ONLY requires at least one blocked predicate")
        if receipt.get("deferred_task_ids") != deferred:
            errors.append("dependency readiness deferred_task_ids must equal the transitive blocked closure")
        if receipt.get("partial_scope_task_ids") != executable:
            errors.append("dependency readiness partial_scope_task_ids must equal the executable complement")
        authorization = receipt.get("partial_authorization")
        if not isinstance(authorization, dict):
            errors.append("PARTIAL_ONLY requires partial_authorization")
        else:
            if authorization.get("parent_thread_id") != data.get("parent_thread_id"):
                errors.append("PARTIAL_ONLY authorization does not bind the current parent thread")
            if not isinstance(authorization.get("quote"), str) or not authorization["quote"].strip():
                errors.append("PARTIAL_ONLY partial_authorization.quote is required")
            if not _timestamp(authorization.get("received_at")):
                errors.append("PARTIAL_ONLY partial_authorization.received_at is invalid")
            if authorization.get("authorized_task_ids") != executable:
                errors.append("PARTIAL_ONLY authorization must bind the exact executable task set")
            if authorization_verifier is None:
                errors.append("PARTIAL_ONLY requires authenticated parent-message verification")
            else:
                errors.extend(authorization_verifier(data, authorization, executable))
    elif status == "BLOCKED":
        errors.append(
            "dependency readiness blocks Plan exit and Implement Orientation: "
            + ", ".join(deferred or sorted({gate["task_id"] for gate in expected}))
        )
    return errors
