#!/usr/bin/env python3
"""Canonical Plan task grammar, linting, and graph policy."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from projection_bundle import (
    projection_sha256,
    validate_projection_bundle,
)

from plan_protocol_core import (
    GRAPH_POLICY_VERSION, PLAN_PROTOCOL_V2, PLAN_REQUIRED_HEADINGS, TASK_FIELDS,
    PlanProtocolError, canonicalize_issue_body, issue_body_sha256, sha256_json,
)
from plan_events import _validate_iso8601

TASKS_PROJECTION_VERSION = "plan-tasks-projection/v1"
GRAPH_POLICY_PROJECTION_VERSION = "graph-policy-projection/v1"
_TASKS_PAYLOAD_FIELDS = frozenset(("adapter_version", "input_digest", "tasks"))
_GRAPH_POLICY_PAYLOAD_FIELDS = frozenset(
    ("adapter_version", "input_digest", "graph_policy")
)


def authority_text(authority_bytes: bytes) -> str:
    if not isinstance(authority_bytes, bytes):
        raise PlanProtocolError("projection authority must be immutable bytes")
    try:
        return authority_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PlanProtocolError("projection authority must be valid UTF-8") from exc


def present_projection_slot(
    payload: Mapping[str, Any], projection_version: str
) -> dict[str, Any]:
    value = dict(payload)
    return {
        "state": "present",
        "projection_version": projection_version,
        "payload": value,
        "payload_digest": projection_sha256(value),
    }


def task_projection_adapter(
    authority_bytes: bytes, authority_digest: str, versions: Mapping[str, str]
) -> dict[str, Any]:
    """Project stable tasks from the kernel's one immutable authority buffer."""

    payload = {
        "adapter_version": TASKS_PROJECTION_VERSION,
        "input_digest": authority_digest,
        "tasks": parse_tasks(authority_text(authority_bytes)),
    }
    return {
        "authority_digest": authority_digest,
        "versions": dict(versions),
        "slot": present_projection_slot(payload, TASKS_PROJECTION_VERSION),
    }


def graph_policy_projection_adapter(*, evaluated_at: str):
    """Return a kernel adapter with its time-dependent policy input frozen."""

    def project(
        authority_bytes: bytes,
        authority_digest: str,
        versions: Mapping[str, str],
    ) -> dict[str, Any]:
        tasks = parse_tasks(authority_text(authority_bytes))
        payload = {
            "adapter_version": GRAPH_POLICY_PROJECTION_VERSION,
            "input_digest": authority_digest,
            "graph_policy": evaluate_graph_policy(tasks, evaluated_at=evaluated_at),
        }
        return {
            "authority_digest": authority_digest,
            "versions": dict(versions),
            "slot": present_projection_slot(payload, GRAPH_POLICY_PROJECTION_VERSION),
        }

    return project


def projection_payload(
    bundle: Mapping[str, Any],
    slot_name: str,
    *,
    projection_version: str,
    payload_fields: frozenset[str],
) -> dict[str, Any]:
    """Consume one closed, digest-bound projection payload from a prepared bundle."""

    errors = validate_projection_bundle(dict(bundle))
    if errors:
        raise PlanProtocolError("invalid projection bundle: " + "; ".join(errors))
    slots = bundle.get("slots")
    slot = slots.get(slot_name) if isinstance(slots, Mapping) else None
    if not isinstance(slot, Mapping) or slot.get("state") != "present":
        raise PlanProtocolError(f"projection slot {slot_name!r} is not present")
    if slot.get("projection_version") != projection_version:
        raise PlanProtocolError(
            f"projection slot {slot_name!r} version is unsupported"
        )
    payload = slot.get("payload")
    if not isinstance(payload, dict) or set(payload) != payload_fields:
        raise PlanProtocolError(
            f"projection slot {slot_name!r} has unknown payload vocabulary"
        )
    if payload.get("adapter_version") != projection_version:
        raise PlanProtocolError(
            f"projection slot {slot_name!r} adapter version is unsupported"
        )
    authority = bundle.get("authority")
    input_digest = authority.get("bytes_digest") if isinstance(authority, Mapping) else None
    if payload.get("input_digest") != input_digest:
        raise PlanProtocolError(
            f"projection slot {slot_name!r} does not share the bundle input digest"
        )
    return payload


def tasks_from_projection_bundle(
    bundle: Mapping[str, Any], slot_name: str = "tasks"
) -> list[dict[str, Any]]:
    payload = projection_payload(
        bundle,
        slot_name,
        projection_version=TASKS_PROJECTION_VERSION,
        payload_fields=_TASKS_PAYLOAD_FIELDS,
    )
    tasks = payload["tasks"]
    if not isinstance(tasks, list):
        raise PlanProtocolError("task projection tasks must be an array")
    return tasks


def graph_policy_from_projection_bundle(
    bundle: Mapping[str, Any], slot_name: str = "graph_policy"
) -> dict[str, Any]:
    payload = projection_payload(
        bundle,
        slot_name,
        projection_version=GRAPH_POLICY_PROJECTION_VERSION,
        payload_fields=_GRAPH_POLICY_PAYLOAD_FIELDS,
    )
    policy = payload["graph_policy"]
    if not isinstance(policy, dict):
        raise PlanProtocolError("graph policy projection must be an object")
    return policy

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


ENTRY_GATE_PREDICATE = re.compile(
    r"(?:phase_receipt:(?:plan|implement|review):VALID|merged_interface:[a-z0-9._/-]+)"
)


def _extract_task(block: str, *, require_entry_gates: bool = False) -> dict[str, Any]:
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
    prefix = flat[: dependency.start()].rstrip()
    entry_gate_match = re.search(r"`entry_gates: (\[.*\])`\.[ \t]*$", prefix)
    if require_entry_gates and entry_gate_match is None:
        raise PlanProtocolError(f"{task_id}: missing exact trailing entry_gates declaration")
    field_text = prefix[: entry_gate_match.start()].rstrip() if entry_gate_match else prefix
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
    entry_gates: list[dict[str, Any]] = []
    if entry_gate_match is not None:
        try:
            raw_entry_gates = entry_gate_match.group(1)
            decoded = json.loads(raw_entry_gates)
        except json.JSONDecodeError as exc:
            raise PlanProtocolError(f"{task_id}: entry_gates must be canonical JSON") from exc
        if not isinstance(decoded, list):
            raise PlanProtocolError(f"{task_id}: entry_gates must be a JSON array")
        canonical_entry_gates = json.dumps(
            decoded, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        if raw_entry_gates != canonical_entry_gates:
            raise PlanProtocolError(
                f"{task_id}: entry_gates must use canonical compact sorted-key JSON"
            )
        for index, gate in enumerate(decoded):
            label = f"{task_id}: entry_gates[{index}]"
            if not isinstance(gate, dict) or set(gate) != {
                "gate_id", "authority_url", "predicates"
            }:
                raise PlanProtocolError(
                    f"{label} must contain exactly gate_id, authority_url, and predicates"
                )
            if not isinstance(gate.get("gate_id"), str) or not re.fullmatch(
                r"G-\d{3}", gate["gate_id"]
            ):
                raise PlanProtocolError(f"{label}.gate_id must match G-NNN")
            if not isinstance(gate.get("authority_url"), str) or not re.fullmatch(
                r"https://github\.com/[^/]+/[^/]+/issues/\d+", gate["authority_url"]
            ):
                raise PlanProtocolError(f"{label}.authority_url must be a canonical GitHub issue URL")
            predicates = gate.get("predicates")
            if not isinstance(predicates, list) or not predicates or any(
                not isinstance(value, str) or not ENTRY_GATE_PREDICATE.fullmatch(value)
                for value in predicates
            ):
                raise PlanProtocolError(f"{label}.predicates contains an unsupported typed predicate")
            if len(predicates) != len(set(predicates)):
                raise PlanProtocolError(f"{label}.predicates contains duplicates")
            entry_gates.append(gate)
        gate_ids = [gate["gate_id"] for gate in entry_gates]
        if len(gate_ids) != len(set(gate_ids)):
            raise PlanProtocolError(f"{task_id}: duplicate entry gate ID")
    normalized_block = canonicalize_issue_body(block)
    return {
        "task_id": task_id,
        "title": title,
        "body": normalized_block,
        "body_sha256": hashlib.sha256(normalized_block.encode("utf-8")).hexdigest(),
        **values,
        "affected_modules": modules,
        "entry_gates": entry_gates,
        "entry_gates_declared": entry_gate_match is not None,
        "depends_on": dependencies,
    }


def parse_tasks(body: str, *, require_entry_gates: bool = False) -> list[dict[str, Any]]:
    tasks = [
        _extract_task(block, require_entry_gates=require_entry_gates)
        for block in _split_task_blocks(_tasks_section(body))
    ]
    ids = [task["task_id"] for task in tasks]
    if len(ids) != len(set(ids)):
        raise PlanProtocolError("task IDs must be unique")
    expected = [f"T-{index:03d}" for index in range(1, len(tasks) + 1)]
    if ids != expected:
        raise PlanProtocolError(f"task IDs must be sequential from T-001; observed={ids}")
    known = set(ids)
    gate_ids = [gate["gate_id"] for task in tasks for gate in task["entry_gates"]]
    if len(gate_ids) != len(set(gate_ids)):
        raise PlanProtocolError("entry gate IDs must be unique across all tasks")
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
            **(
                {"entry_gates": task["entry_gates"]}
                if task.get("entry_gates_declared")
                else {}
            ),
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
