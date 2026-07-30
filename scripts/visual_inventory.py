#!/usr/bin/env python3
"""Authoritative visual-scope extraction and Plan inventory building."""
from __future__ import annotations

import re
from typing import Any

from plan_protocol import PlanProtocolError, parse_tasks
from visual_core import (
    DOMAIN_PREFIXES,
    REQUIRED_DOMAINS,
    RUNTIME_KINDS,
    canonical_sha256,
    _declared_kind_group,
    _direction_entry,
    _inferred_kind_group,
    _intent_group,
    _sequential,
    markdown_section,
)

def extract_scope_inventory(
    body: str, *, user_directions: list[str] | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Derive the Plan issue's stable inventory used by the integration gate.

    The Plan's explicit scope-accounting declaration is the positive evidence
    that its entries are nonvisual. This parser does not infer that an arbitrary
    issue is nonvisual merely because no visual word happens to appear.
    """

    errors: list[str] = []
    acceptance = markdown_section(body, "Acceptance Criteria")
    tasks = markdown_section(body, "Tasks")
    ac_ids = re.findall(r"<!--\s*(AC-\d{3})\s*-->", acceptance)
    if not ac_ids or len(ac_ids) != len(set(ac_ids)) or not _sequential(ac_ids, "AC"):
        errors.append("visual scope inventory requires unique sequential AC-NNN markers")

    try:
        canonical_tasks = parse_tasks(body)
    except PlanProtocolError:
        canonical_tasks = None
    if canonical_tasks is None:
        task_records = [
            {"source": line, "task_id": None, "affected_modules": None}
            for line in re.findall(
                r"(?im)^[ \t]*[-*][ \t]+\[[ xX]\][ \t]+.+$", tasks
            )
        ]
    else:
        task_records = [
            {
                "source": task["body"],
                "task_id": task["task_id"],
                "affected_modules": task["affected_modules"],
            }
            for task in canonical_tasks
        ]
    task_ids: list[str] = []
    task_entries: list[dict[str, str]] = []
    modules: list[dict[str, str]] = []
    for record in task_records:
        source = record["source"]
        task_match = re.search(r"\b(T-\d{3})\b", source)
        task_id = record["task_id"] or (
            task_match.group(1) if task_match is not None else None
        )
        if task_id is None:
            errors.append("every task must contain a stable T-NNN marker")
            continue
        task_ids.append(task_id)
        entries = record["affected_modules"]
        modules_match = None
        if entries is None:
            modules_match = re.search(
                r"Affected modules:\s*(.*?)\.\s+Requirements:",
                source,
                re.IGNORECASE,
            )
            if modules_match is None:
                errors.append(f"{task_id} lacks a parseable Affected modules clause")
                continue
            entries = [
                entry.strip()
                for entry in modules_match.group(1).split(",")
                if entry.strip()
            ]
        if not entries:
            errors.append(f"{task_id} has no affected-module entries")
        for entry in entries:
            inferred = _inferred_kind_group({"source": entry})
            if inferred is None:
                errors.append(
                    f"{task_id} affected module {entry!r} cannot be independently classified"
                )
                kind = "nonvisual"
            elif inferred == "runtime":
                kind = "existing_component_state"
            elif inferred == "generative":
                kind = "new_visual_concept"
            else:
                kind = "nonvisual"
            modules.append(
                {
                    "id": f"M-{len(modules) + 1:03d}",
                    "task_id": task_id,
                    "source": entry,
                    "kind": kind,
                    "provenance": f"authoritative task {task_id} affected-modules text",
                    **(
                        {"runtime_evidence_sufficient": True}
                        if kind in RUNTIME_KINDS
                        else {}
                    ),
                }
            )
        module_groups = {
            _declared_kind_group(module["kind"])
            for module in modules
            if module["task_id"] == task_id
        }
        if canonical_tasks is not None:
            task_intent = source
        else:
            assert modules_match is not None
            intent_match = re.match(
                r"(?i)^[ \t]*[-*][ \t]+\[[ xX]\][ \t]+\*\*T-\d{3}[^\n]*?\*\*"
                r"(.*?)(?=Affected modules:)",
                source,
            )
            task_intent = (
                re.sub(r"[*`]", "", source[: modules_match.start()])
                if intent_match is None
                else re.sub(r"[*`]", "", intent_match.group(0))
            )
        intent_group = _intent_group(task_intent)
        if intent_group == "generative":
            task_group = "generative"
        elif intent_group == "runtime":
            task_group = "runtime"
        elif "runtime" in module_groups:
            task_group = (
                "runtime"
            )
        else:
            task_group = "nonvisual"
        task_entries.append(
            {
                "id": task_id,
                "kind": (
                    "new_visual_concept"
                    if task_group == "generative"
                    else "existing_component_state"
                    if task_group == "runtime"
                    else "nonvisual"
                ),
                "source": source.strip(),
                "provenance": f"authoritative Tasks entry {task_id}",
                "classification_basis": "affected_modules",
                **(
                    {"runtime_evidence_sufficient": True}
                    if task_group == "runtime"
                    else {}
                ),
            }
        )
    if not task_ids or len(task_ids) != len(set(task_ids)) or not _sequential(task_ids, "T"):
        errors.append("visual scope inventory requires unique sequential T-NNN markers")

    declarations = {
        "deliverables": re.search(r"`D-001`", body) is not None,
        "user_directions": re.search(r"`UD-001`", body) is not None,
        "acceptance_criteria": re.search(
            rf"`AC-001`\s+through\s+`AC-{len(ac_ids):03d}`", body
        )
        is not None,
        "tasks": re.search(rf"`T-001`\s+through\s+`T-{len(task_ids):03d}`", body)
        is not None,
        "affected_modules": re.search(
            rf"`M-001`\s+through\s+`M-{len(modules):03d}`", body
        )
        is not None,
    }
    for domain, present in declarations.items():
        if not present:
            errors.append(f"visual scope inventory declaration is missing exact {domain} coverage")

    problem = markdown_section(body, "Problem Statement")
    design = markdown_section(body, "Design")
    deliverable_source = problem or design
    deliverable_group = _intent_group(deliverable_source)
    task_groups = {
        _declared_kind_group(entry["kind"]) for entry in task_entries
    }
    overall_group = (
        "generative"
        if deliverable_group == "generative" or "generative" in task_groups
        else "runtime"
        if deliverable_group == "runtime"
        or "runtime" in task_groups
        or any(
            _declared_kind_group(module["kind"]) == "runtime" for module in modules
        )
        else "nonvisual"
    )
    overall_visual = overall_group != "nonvisual"
    acceptance_entries = []
    for value in ac_ids:
        source_match = re.search(
            rf"(?im)^[^\n]*<!--\s*{re.escape(value)}\s*-->[^\n]*$", acceptance
        )
        source = source_match.group(0).strip() if source_match else value
        source_group = _intent_group(source)
        kind = "nonvisual"
        if source_group == "generative":
            kind = "new_visual_concept"
        elif source_group == "runtime":
            kind = "existing_component_state"
        acceptance_entries.append(
            {
                "id": value,
                "kind": kind,
                "source": source,
                "provenance": f"authoritative Acceptance Criteria marker {value}",
                "classification_basis": "affected_modules",
                **(
                    {"runtime_evidence_sufficient": True}
                    if kind in RUNTIME_KINDS
                    else {}
                ),
            }
        )
    inventory: dict[str, Any] = {
        "deliverables": [
            {
                "id": "D-001",
                "kind": (
                    "new_visual_concept"
                    if overall_group == "generative"
                    else "existing_component_state"
                    if overall_group == "runtime"
                    else "nonvisual"
                ),
                "source": deliverable_source,
                "provenance": "authoritative Plan body",
                "classification_basis": "affected_modules",
                **(
                    {"runtime_evidence_sufficient": True}
                    if overall_visual
                    else {}
                ),
            }
        ],
        "user_directions": [
            _direction_entry(f"UD-{index:03d}", source, index)
            for index, source in enumerate(
                user_directions or ["legacy direction unavailable"], 1
            )
        ],
        "acceptance_criteria": acceptance_entries,
        "tasks": task_entries,
        "affected_modules": modules,
    }
    return inventory, errors


def inventory_sha256(inventory: dict[str, Any]) -> str:
    return canonical_sha256(inventory)


def build_plan_inventory(
    body: str, *, user_directions: list[str] | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Build the v2 Plan inventory from canonical issue text."""

    inventory, errors = extract_scope_inventory(
        body, user_directions=user_directions
    )
    seen_paths: set[str] = set()
    planned_paths: list[dict[str, Any]] = []
    for module in inventory.get("affected_modules", []):
        path = str(module.get("source", "")).strip("` ")
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)
        planned_paths.append(
            {
                "id": f"P-{len(planned_paths) + 1:03d}",
                "path": path,
                "kind": module.get("kind"),
                "provenance": module.get("provenance"),
                **(
                    {
                        "runtime_evidence_sufficient": module[
                            "runtime_evidence_sufficient"
                        ]
                    }
                    if isinstance(module.get("runtime_evidence_sufficient"), bool)
                    else {}
                ),
            }
        )
    inventory["planned_paths"] = planned_paths
    return inventory, errors
