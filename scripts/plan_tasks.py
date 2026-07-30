#!/usr/bin/env python3
"""Canonical Plan task grammar, linting, and graph policy."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from plan_protocol_core import (
    GRAPH_POLICY_VERSION, PLAN_PROTOCOL_V2, PLAN_REQUIRED_HEADINGS, TASK_FIELDS,
    PlanProtocolError, canonicalize_issue_body, issue_body_sha256, sha256_json,
)
from plan_events import _validate_iso8601

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
