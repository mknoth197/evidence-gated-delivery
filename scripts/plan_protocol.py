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
        "plan_protocol_version": PLAN_PROTOCOL_V2,
        "workflow_version": WORKFLOW_VERSION_V2,
        "repo_root": manifest.get("repo_root"),
        "starting_commit": manifest.get("starting_commit"),
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
    if not isinstance(run_id, str) or not run_id.strip():
        return False, []
    path = protocol_activation_receipt_path(run_id)
    if not path.exists():
        return False, []
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
    if receipt.get("run_id") != run_id.strip():
        errors.append("external v2 activation receipt run_id mismatch")
    if receipt.get("plan_protocol_version") != PLAN_PROTOCOL_V2:
        errors.append("external activation receipt must bind plan-protocol/v2")
    if receipt.get("workflow_version") != WORKFLOW_VERSION_V2:
        errors.append("external activation receipt must bind the v2 workflow")
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


def _tasks_section(body: str) -> str:
    normalized = canonicalize_issue_body(body)
    matches = list(re.finditer(r"(?m)^## Tasks[ \t]*\n", normalized))
    if len(matches) != 1:
        raise PlanProtocolError("authoritative issue must contain exactly one '## Tasks' section")
    start = matches[0].end()
    end_match = re.search(r"(?m)^## (?!#)", normalized[start:])
    return normalized[start : start + end_match.start() if end_match else len(normalized)]


def _split_task_blocks(section: str) -> list[str]:
    starts = list(re.finditer(r"(?m)^- \[[ xX]\] \*\*T-[^\n]+", section))
    stray = re.findall(r"(?m)^[ \t]+- \[[ xX]\] \*\*T-[^\n]+", section)
    if stray:
        raise PlanProtocolError("tasks must be direct Markdown tasks, not nested list items")
    if not starts:
        raise PlanProtocolError("'## Tasks' must contain at least one direct task")
    blocks: list[str] = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(section)
        block = section[match.start() : end].rstrip("\n")
        # Non-task content between direct tasks belongs to the preceding task. A new
        # direct list item is ambiguous and therefore rejected.
        if re.search(r"(?m)^- (?!\[[ xX]\] \*\*T-)", block):
            raise PlanProtocolError("unexpected direct list item inside task block")
        blocks.append(block)
    return blocks


def _extract_task(block: str) -> dict[str, Any]:
    header = re.match(
        r"^- \[ \] \*\*(T-\d{3}) — ([^*\n]+?)\.\*\*[ \t]*(.*)$",
        block,
        re.DOTALL,
    )
    if not header:
        raise PlanProtocolError(
            "task header must match '- [ ] **T-NNN — Title.**' using an em dash"
        )
    task_id, title, remainder = header.groups()
    flat = re.sub(r"\n[ \t]+", " ", remainder).strip()
    dependency = re.search(r"`depends_on: \[([^\]]*)\]`\.[ \t]*$", flat)
    if not dependency:
        raise PlanProtocolError(f"{task_id}: missing exact trailing depends_on declaration")
    field_text = flat[: dependency.start()].rstrip()
    positions: list[tuple[int, int, str, str]] = []
    for label, key in TASK_FIELDS:
        # Issue #2's already-audited task form uses "Complete when ..." while the
        # protocol example uses "Complete when: ..."; both delimit the same field.
        separator = r"(?::[ \t]*|[ \t]+)" if label == "Complete when" else r":[ \t]*"
        found = list(
            re.finditer(rf"(?<!\w){re.escape(label)}{separator}", field_text)
        )
        if len(found) != 1:
            raise PlanProtocolError(f"{task_id}: field {label!r} must appear exactly once")
        positions.append((found[0].start(), found[0].end(), label, key))
    positions.sort()
    if [item[2] for item in positions] != [item[0] for item in TASK_FIELDS]:
        raise PlanProtocolError(f"{task_id}: required fields are out of order")
    if positions[0][0] != 0:
        raise PlanProtocolError(f"{task_id}: unexpected content before Objective")
    values: dict[str, Any] = {}
    for index, (_start, value_start, label, key) in enumerate(positions):
        value_end = positions[index + 1][0] if index + 1 < len(positions) else len(field_text)
        value = field_text[value_start:value_end].strip()
        if value.endswith("."):
            value = value[:-1].rstrip()
        if not value:
            raise PlanProtocolError(f"{task_id}: field {label!r} must not be empty")
        values[key] = value
    modules = [part.strip().strip("`") for part in values["affected_modules"].split(",")]
    if not modules or any(not module for module in modules):
        raise PlanProtocolError(f"{task_id}: affected modules must be a non-empty comma-separated list")
    raw_dependencies = dependency.group(1).strip()
    dependencies = (
        [] if not raw_dependencies else [item.strip() for item in raw_dependencies.split(",")]
    )
    if any(not re.fullmatch(r"T-\d{3}", item) for item in dependencies):
        raise PlanProtocolError(f"{task_id}: malformed dependency ID")
    if len(dependencies) != len(set(dependencies)):
        raise PlanProtocolError(f"{task_id}: duplicate dependency ID")
    normalized_block = canonicalize_issue_body(block)
    return {
        "task_id": task_id,
        "title": title,
        "body": normalized_block,
        "body_sha256": hashlib.sha256(normalized_block.encode("utf-8")).hexdigest(),
        **values,
        "affected_modules": modules,
        "depends_on": dependencies,
    }


def parse_tasks(body: str) -> list[dict[str, Any]]:
    tasks = [_extract_task(block) for block in _split_task_blocks(_tasks_section(body))]
    ids = [task["task_id"] for task in tasks]
    if len(ids) != len(set(ids)):
        raise PlanProtocolError("task IDs must be unique")
    expected = [f"T-{index:03d}" for index in range(1, len(tasks) + 1)]
    if ids != expected:
        raise PlanProtocolError(f"task IDs must be sequential from T-001; observed={ids}")
    known = set(ids)
    for task in tasks:
        task_id = task["task_id"]
        for dependency in task["depends_on"]:
            if dependency == task_id:
                raise PlanProtocolError(f"{task_id}: task cannot depend on itself")
            if dependency not in known:
                raise PlanProtocolError(f"{task_id}: dependency {dependency} does not exist")
    visiting: set[str] = set()
    visited: set[str] = set()
    by_id = {task["task_id"]: task for task in tasks}

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise PlanProtocolError(f"task dependency graph contains a cycle at {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in by_id[task_id]["depends_on"]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in ids:
        visit(task_id)
    return tasks


def task_set_sha256(tasks: list[dict[str, Any]]) -> str:
    stable = [
        {
            "task_id": task["task_id"],
            "body_sha256": task["body_sha256"],
            "owner_lane": task["owner_lane"],
            "depends_on": task["depends_on"],
        }
        for task in tasks
    ]
    return sha256_json(stable)


def evaluate_graph_policy(
    tasks: list[dict[str, Any]], *, evaluated_at: str | None = None
) -> dict[str, Any]:
    timestamp = evaluated_at or _utc_now()
    _validate_iso8601(timestamp, "evaluated_at")
    edges = sum(len(task["depends_on"]) for task in tasks)
    lanes = sorted({task["owner_lane"] for task in tasks})
    disposition = (
        "NO_GRAPH"
        if len(tasks) <= 3 and edges == 0 and len(lanes) == 1
        else "GRAPH_REQUIRED"
    )
    return {
        "policy_version": GRAPH_POLICY_VERSION,
        "disposition": disposition,
        "task_count": len(tasks),
        "edge_count": edges,
        "owner_lanes": lanes,
        "task_set_sha256": task_set_sha256(tasks),
        "evaluated_at": timestamp,
    }


def lint_plan(body: str) -> dict[str, Any]:
    digest = issue_body_sha256(body)
    findings: list[dict[str, str]] = []
    normalized = canonicalize_issue_body(body)
    headings = re.findall(r"(?m)^## ([^\n]+?)[ \t]*$", normalized)
    missing = [heading for heading in PLAN_REQUIRED_HEADINGS if headings.count(heading) != 1]
    if missing:
        findings.append(
            {
                "finding_id": "LINT-001",
                "severity": "Blocker",
                "evidence": f"required headings must appear exactly once: {missing}",
                "candidate_body_sha256": digest,
            }
        )
    acceptance_match = re.search(
        r"(?ms)^## Acceptance Criteria[ \t]*\n(.*?)(?=^## |\Z)",
        normalized,
    )
    acceptance = acceptance_match.group(1) if acceptance_match else ""
    if not re.search(
        r"(?im)^(?:[-*][ \t]+)?(?:WHEN|WHILE|WHERE|IF)\b.+\bSHALL\b.+$",
        acceptance,
    ):
        findings.append(
            {
                "finding_id": "LINT-002",
                "severity": "High",
                "evidence": "Acceptance Criteria lacks an EARS-style SHALL statement",
                "candidate_body_sha256": digest,
            }
        )
    design_match = re.search(
        r"(?ms)^## Design[ \t]*\n(.*?)(?=^## |\Z)",
        normalized,
    )
    if design_match is None or "```mermaid" not in design_match.group(1).lower():
        findings.append(
            {
                "finding_id": "LINT-003",
                "severity": "Medium",
                "evidence": "Design must contain a Mermaid diagram",
                "candidate_body_sha256": digest,
            }
        )
    if re.search(r"(?m)^- \[[xX]\] \*\*T-", normalized):
        findings.append(
            {
                "finding_id": "LINT-004",
                "severity": "High",
                "evidence": "Plan tasks must remain unchecked before implementation",
                "candidate_body_sha256": digest,
            }
        )
    try:
        tasks = parse_tasks(body)
    except PlanProtocolError as exc:
        findings.append(
            {
                "finding_id": "LINT-005",
                "severity": "Blocker",
                "evidence": str(exc),
                "candidate_body_sha256": digest,
            }
        )
        tasks = []
    return {
        "status": "PASS" if not findings else "FAIL",
        "protocol_version": PLAN_PROTOCOL_V2,
        "candidate_body_sha256": digest,
        "task_count": len(tasks),
        "task_set_sha256": task_set_sha256(tasks) if tasks else None,
        "findings": findings,
    }


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
    record_protocol_activation(migrated, migration_event)
    manifest.clear()
    manifest.update(migrated)
    return manifest


def _audit_result_hash(audit: dict[str, Any]) -> str:
    return sha256_json({key: value for key, value in audit.items() if key != "result_sha256"})


def validate_finding(finding: Any, prefix: str = "finding") -> list[str]:
    if not isinstance(finding, dict):
        return [f"{prefix} must be an object"]
    errors: list[str] = []
    required_text = (
        "finding_id",
        "evidence",
        "targeted_patch",
        "verification_implication",
        "downstream_instruction",
    )
    for field in required_text:
        if not isinstance(finding.get(field), str) or not finding[field].strip():
            errors.append(f"{prefix}.{field} must be non-empty text")
    if finding.get("severity") not in FINDING_SEVERITIES:
        errors.append(f"{prefix}.severity is unsupported")
    confidence = finding.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 10:
        errors.append(f"{prefix}.confidence must be a number from 0 through 10")
    disposition = finding.get("disposition")
    if disposition not in FINDING_DISPOSITIONS:
        errors.append(f"{prefix}.disposition is unsupported")
    question = finding.get("bounded_question")
    if question is not None and (not isinstance(question, str) or not question.strip()):
        errors.append(f"{prefix}.bounded_question must be null or non-empty text")
    if disposition in ("accepted", "deferred"):
        for field in ("owner", "rationale"):
            if not isinstance(finding.get(field), str) or not finding[field].strip():
                errors.append(f"{prefix}.{field} is required for {disposition}")
    if finding.get("severity") in ("Blocker", "High") and disposition in ("accepted", "deferred"):
        errors.append(f"{prefix}: Blocker/High findings cannot be accepted or deferred")
    return errors


def validate_plan_audits(
    audits: Any,
    *,
    final_body_sha256: str,
    disallowed_agent_ids: Iterable[str] = (),
) -> list[str]:
    if not isinstance(audits, list) or not audits:
        return ["plan_audits must be a non-empty array"]
    errors: list[str] = []
    disallowed = {str(value) for value in disallowed_agent_ids}
    seen_agents: set[str] = set()
    seen_audits: dict[str, dict[str, Any]] = {}
    unresolved: dict[str, dict[str, Any]] = {}
    final_remote = False
    for index, audit in enumerate(audits):
        prefix = f"plan_audits[{index}]"
        if not isinstance(audit, dict):
            errors.append(f"{prefix} must be an object")
            continue
        audit_id = audit.get("audit_id")
        if not isinstance(audit_id, str) or not audit_id:
            errors.append(f"{prefix}.audit_id must be non-empty")
        elif audit_id in seen_audits:
            errors.append(f"{prefix}.audit_id must be unique")
        agent_id = audit.get("agent_id")
        try:
            uuid.UUID(str(agent_id))
        except (ValueError, AttributeError):
            errors.append(f"{prefix}.agent_id must be a UUID")
        agent_key = str(agent_id)
        if agent_key in seen_agents or agent_key in disallowed:
            errors.append(f"{prefix}.agent_id violates role/session separation")
        seen_agents.add(agent_key)
        if audit.get("agent_path") in (None, ""):
            errors.append(f"{prefix}.agent_path must be non-empty")
        if audit.get("role_marker") != "Independent Plan spec auditor":
            errors.append(f"{prefix}.role_marker is invalid")
        kind = audit.get("kind")
        if kind not in AUDIT_KINDS:
            errors.append(f"{prefix}.kind is unsupported")
        reviewed_hash = audit.get("reviewed_body_sha256")
        if not re.fullmatch(r"[0-9a-f]{64}", str(reviewed_hash)):
            errors.append(f"{prefix}.reviewed_body_sha256 must be lowercase SHA-256")
        for field in ("started_at", "completed_at"):
            try:
                _validate_iso8601(audit.get(field), f"{prefix}.{field}")
            except PlanProtocolError as exc:
                errors.append(str(exc))
        if isinstance(audit.get("started_at"), str) and isinstance(audit.get("completed_at"), str):
            try:
                if datetime.fromisoformat(audit["completed_at"].replace("Z", "+00:00")) < datetime.fromisoformat(audit["started_at"].replace("Z", "+00:00")):
                    errors.append(f"{prefix}.completed_at precedes started_at")
            except ValueError:
                pass
        evidence_ids = audit.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids or any(not isinstance(v, str) or not v for v in evidence_ids):
            errors.append(f"{prefix}.evidence_ids must be a non-empty text array")
        findings = audit.get("findings")
        if not isinstance(findings, list):
            errors.append(f"{prefix}.findings must be an array")
            findings = []
        finding_ids: set[str] = set()
        for finding_index, finding in enumerate(findings):
            errors.extend(validate_finding(finding, f"{prefix}.findings[{finding_index}]"))
            finding_id = finding.get("finding_id") if isinstance(finding, dict) else None
            finding_key = str(finding_id)
            if finding_key in finding_ids:
                errors.append(f"{prefix}: finding IDs must be unique within an audit")
            finding_ids.add(finding_key)
            if isinstance(finding, dict) and finding.get("severity") in ("Blocker", "High", "Medium"):
                if finding.get("disposition") == "open":
                    unresolved[str(finding.get("finding_id", ""))] = finding
        predecessor_audit_id = audit.get("predecessor_audit_id")
        predecessor_finding_ids = audit.get("predecessor_finding_ids", [])
        if kind == "remediation_recheck":
            if predecessor_audit_id not in seen_audits:
                errors.append(f"{prefix}: remediation recheck requires an earlier predecessor audit")
            if not isinstance(predecessor_finding_ids, list) or not predecessor_finding_ids:
                errors.append(f"{prefix}.predecessor_finding_ids must be non-empty")
            else:
                predecessor = seen_audits.get(predecessor_audit_id, {})
                prior_ids = {
                    finding.get("finding_id")
                    for finding in predecessor.get("findings", [])
                    if isinstance(finding, dict)
                }
                if any(not isinstance(value, str) for value in predecessor_finding_ids) or not set(predecessor_finding_ids).issubset(prior_ids):
                    errors.append(f"{prefix}: predecessor finding lineage is invalid")
                rechecked = {
                    finding.get("finding_id"): finding
                    for finding in findings
                    if isinstance(finding, dict)
                }
                for finding_id in predecessor_finding_ids:
                    finding = rechecked.get(finding_id)
                    if not finding or finding.get("disposition") != "verified_fixed":
                        errors.append(f"{prefix}: {finding_id} lacks verified_fixed recheck")
                    else:
                        unresolved.pop(finding_id, None)
        elif predecessor_audit_id not in (None, "") or predecessor_finding_ids:
            errors.append(f"{prefix}: predecessor lineage is only valid on remediation_recheck")
        if kind == "final_remote" and reviewed_hash == final_body_sha256:
            final_remote = True
        try:
            if audit.get("result_sha256") != _audit_result_hash(audit):
                errors.append(f"{prefix}.result_sha256 does not match immutable audit content")
        except (TypeError, ValueError):
            errors.append(f"{prefix} contains non-JSON audit content")
        if isinstance(audit_id, str):
            seen_audits[audit_id] = audit
    for finding_id, finding in sorted(unresolved.items()):
        severity = finding.get("severity")
        disposition = finding.get("disposition")
        if severity in ("Blocker", "High") or (
            severity == "Medium" and disposition not in ("accepted", "deferred")
        ):
            errors.append(f"finding {finding_id!r} remains unresolved")
    if not final_remote:
        errors.append("a fresh final_remote audit must bind the exact canonical remote issue body")
    elif not isinstance(audits[-1], dict) or audits[-1].get("kind") != "final_remote" or audits[-1].get("reviewed_body_sha256") != final_body_sha256:
        errors.append("the latest Plan audit must be final_remote and bind the exact remote body")
    return errors


def graph_edges(tasks: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"blocked": task["task_id"], "blocked_by": dependency}
        for task in tasks
        for dependency in task["depends_on"]
    ]


def freeze_graph_draft(
    parent_issue_url: str, repository: str, tasks: list[dict[str, Any]]
) -> dict[str, Any]:
    if not parent_issue_url.startswith("https://github.com/"):
        raise PlanProtocolError("parent issue URL must be an HTTPS GitHub URL")
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
        raise PlanProtocolError("repository must use owner/name form")
    children = [
        _graph_child(task)
        for task in tasks
    ]
    draft = {
        "schema_version": "graph-draft/v1",
        "parent_issue_url": parent_issue_url,
        "repository": repository,
        "children": children,
        "edges": graph_edges(tasks),
    }
    draft["draft_sha256"] = sha256_json(draft)
    return draft


def _graph_child(task: dict[str, Any]) -> dict[str, Any]:
    marker = f"<!-- evidence-gated-delivery-task:{task['task_id']} -->"
    body = f"{marker}\n\n{task['body']}"
    return {
        "task_id": task["task_id"],
        "stable_marker": marker,
        "title": task["title"],
        "body": body,
        "body_sha256": issue_body_sha256(body),
    }


def validate_graph_draft(draft: Any) -> list[str]:
    if not isinstance(draft, dict):
        return ["graph draft must be an object"]
    errors: list[str] = []
    if draft.get("schema_version") != "graph-draft/v1":
        errors.append("graph draft schema_version is unsupported")
    children = draft.get("children")
    edges = draft.get("edges")
    if not isinstance(children, list) or not children:
        errors.append("graph draft children must be a non-empty array")
        children = []
    if not isinstance(edges, list):
        errors.append("graph draft edges must be an array")
        edges = []
    ids: list[str] = []
    for index, child in enumerate(children):
        if not isinstance(child, dict):
            errors.append(f"graph draft child {index} must be an object")
            continue
        task_id = child.get("task_id")
        ids.append(task_id)
        if child.get("stable_marker") != f"<!-- evidence-gated-delivery-task:{task_id} -->":
            errors.append(f"graph draft child {task_id} has invalid stable marker")
        try:
            if issue_body_sha256(child.get("body", "")) != child.get("body_sha256"):
                errors.append(f"graph draft child {task_id} body hash mismatch")
        except PlanProtocolError:
            errors.append(f"graph draft child {task_id} body must be text")
    normalized_ids = [str(value) for value in ids]
    if len(normalized_ids) != len(set(normalized_ids)):
        errors.append("graph draft task IDs must be unique")
    expected_edge_keys: set[tuple[str, str]] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            errors.append("graph draft edge must be an object")
            continue
        pair = (str(edge.get("blocked")), str(edge.get("blocked_by")))
        if pair in expected_edge_keys:
            errors.append("graph draft edges must be unique")
        expected_edge_keys.add(pair)
        if any(task_id not in normalized_ids for task_id in pair):
            errors.append("graph draft edge references an unknown task")
    try:
        expected_hash = sha256_json({key: value for key, value in draft.items() if key != "draft_sha256"})
        if draft.get("draft_sha256") != expected_hash:
            errors.append("graph draft SHA-256 mismatch")
    except (TypeError, ValueError):
        errors.append("graph draft contains non-JSON content")
    return errors


def verify_graph_authorization(
    authorization: Any,
    draft: dict[str, Any],
    *,
    current_login: str,
    current_account_id: str,
    current_repository: str,
    current_parent_issue_url: str,
    capability_receipt: dict[str, Any],
) -> list[str]:
    errors = validate_graph_draft(draft)
    if not isinstance(authorization, dict):
        return errors + ["graph authorization must be an object"]
    if errors:
        return errors
    if not isinstance(capability_receipt, dict):
        return ["capability receipt must be an object"]
    required_matches = {
        "github_login": current_login,
        "github_account_id": current_account_id,
        "repository": current_repository,
        "parent_issue_url": current_parent_issue_url,
        "draft_sha256": draft.get("draft_sha256"),
        "capability_receipt_sha256": sha256_json(capability_receipt),
        "child_body_sha256s": [child["body_sha256"] for child in draft.get("children", [])],
        "edges": draft.get("edges", []),
    }
    for field, expected in required_matches.items():
        if authorization.get(field) != expected:
            errors.append(f"graph authorization {field} does not match current transaction")
    if current_repository != draft.get("repository"):
        errors.append("current repository does not match frozen draft")
    if current_parent_issue_url != draft.get("parent_issue_url"):
        errors.append("current parent issue does not match frozen draft")
    capability_matches = {
        "github_login": current_login,
        "github_account_id": current_account_id,
        "repository": current_repository,
        "parent_issue_url": current_parent_issue_url,
        "native_parent_supported": True,
        "blocking_supported": True,
        "readback_supported": True,
    }
    for field, expected in capability_matches.items():
        if capability_receipt.get(field) != expected:
            errors.append(f"capability receipt {field} is missing or stale")
    if not isinstance(authorization.get("authorization_evidence"), str) or not authorization["authorization_evidence"].strip():
        errors.append("graph authorization requires explicit authorization evidence")
    try:
        _validate_iso8601(authorization.get("authorized_at"), "graph authorization authorized_at")
    except PlanProtocolError as exc:
        errors.append(str(exc))
    return errors


def reconcile_graph_state(
    draft: dict[str, Any], remote_state: dict[str, Any]
) -> dict[str, Any]:
    draft_errors = validate_graph_draft(draft)
    if draft_errors:
        raise PlanProtocolError("invalid graph draft: " + "; ".join(draft_errors))
    if not isinstance(remote_state, dict):
        return {"classification": "CONFLICT", "reasons": ["remote readback is not an object"]}
    remote_children = remote_state.get("children")
    remote_edges = remote_state.get("edges")
    if not isinstance(remote_children, list) or not isinstance(remote_edges, list):
        return {"classification": "CONFLICT", "reasons": ["remote readback is incomplete"]}
    expected_children = {child["task_id"]: child for child in draft["children"]}
    observed_ids: set[str] = set()
    reasons: list[str] = []
    for child in remote_children:
        if not isinstance(child, dict):
            reasons.append("remote child is malformed")
            continue
        task_id = child.get("task_id")
        if not isinstance(task_id, str):
            reasons.append("remote child has malformed task ID")
            continue
        if task_id in observed_ids:
            reasons.append(f"duplicate remote stable marker for {task_id}")
            continue
        observed_ids.add(task_id)
        expected = expected_children.get(task_id)
        if expected is None:
            reasons.append(f"extra remote child {task_id}")
            continue
        for field in ("stable_marker", "title", "body_sha256"):
            if child.get(field) != expected[field]:
                reasons.append(f"remote child {task_id} {field} conflicts")
        if child.get("parent_issue_url") != draft["parent_issue_url"]:
            reasons.append(f"remote child {task_id} parent conflicts")
    expected_child_order = [child["task_id"] for child in draft["children"]]
    observed_order = [
        child.get("task_id")
        for child in remote_children
        if isinstance(child, dict) and child.get("task_id") in expected_children
    ]
    child_positions = [expected_child_order.index(task_id) for task_id in observed_order]
    if child_positions != sorted(child_positions):
        reasons.append("remote children are reordered")
    expected_edges = [(edge["blocked"], edge["blocked_by"]) for edge in draft["edges"]]
    observed_edges: list[tuple[Any, Any]] = []
    for edge in remote_edges:
        if not isinstance(edge, dict):
            reasons.append("remote edge is malformed")
            continue
        blocked, blocked_by = edge.get("blocked"), edge.get("blocked_by")
        if not isinstance(blocked, str) or not isinstance(blocked_by, str):
            reasons.append("remote edge has malformed task ID")
            continue
        observed_edges.append((blocked, blocked_by))
    if len(observed_edges) != len(set(observed_edges)):
        reasons.append("duplicate remote dependency edge")
    if any(edge not in expected_edges for edge in observed_edges):
        reasons.append("remote dependency graph contains an extra or conflicting edge")
    # Remote order must be a subsequence of authorized order.
    edge_positions = [expected_edges.index(edge) for edge in observed_edges if edge in expected_edges]
    if edge_positions != sorted(edge_positions):
        reasons.append("remote dependency edges are reordered")
    if reasons:
        return {"classification": "CONFLICT", "reasons": sorted(set(reasons))}
    missing_children = [
        child["task_id"] for child in draft["children"] if child["task_id"] not in observed_ids
    ]
    missing_edges = [
        {"blocked": edge[0], "blocked_by": edge[1]}
        for edge in expected_edges
        if edge not in observed_edges
    ]
    if not missing_children and not missing_edges:
        return {"classification": "EXACT_MATCH", "missing_children": [], "missing_edges": []}
    return {
        "classification": "AUTHORIZED_MISSING",
        "missing_children": missing_children,
        "missing_edges": missing_edges,
    }


def verify_final_graph(
    draft: dict[str, Any],
    remote_state: dict[str, Any],
    action_records: Any,
) -> list[str]:
    reconciliation = reconcile_graph_state(draft, remote_state)
    if reconciliation["classification"] != "EXACT_MATCH":
        return [f"remote graph is not exact: {reconciliation}"]
    if not isinstance(action_records, list):
        return ["graph action records must be an array"]
    errors: list[str] = []
    verified: set[tuple[str, str]] = set()
    attempted: set[tuple[str, str]] = set()
    for index, action in enumerate(action_records):
        if not isinstance(action, dict):
            errors.append(f"graph action {index} must be an object")
            continue
        status = action.get("status")
        if status not in ("attempted", "verified", "blocked"):
            errors.append(f"graph action {index} has invalid status")
            continue
        raw_kind = action.get("kind")
        kind = {"create_child": "child", "add_blocked_by": "edge"}.get(
            raw_kind, raw_kind
        )
        key = (kind, action.get("key", action.get("target")))
        if status == "attempted":
            attempted.add(key)
        elif status == "verified":
            if key not in attempted:
                errors.append(f"graph action {index} was verified before attempted")
            verified.add(key)
    expected = {
        ("child", child["task_id"]) for child in draft["children"]
    } | {
        ("edge", f"{edge['blocked']}<-{edge['blocked_by']}") for edge in draft["edges"]
    }
    missing = expected - verified
    if missing:
        errors.append(f"graph actions lack verified records: {sorted(missing)}")
    return errors
