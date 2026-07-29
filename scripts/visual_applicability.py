#!/usr/bin/env python3
"""Deterministic visual-applicability policy and receipt validation.

The policy consumes explicit, provenance-bearing scope evidence. Repository-wide
signals are never sufficient on their own: a frontend, CSS file, or web
framework only matters when it is represented by an in-scope inventory entry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

POLICY_VERSION = "visual-applicability/v1"
BLOCKED_DECISION = "BLOCKED_PENDING_VISUAL_CLARIFICATION"
DECISION_BY_MODE = {
    "none": "VISUAL_NOT_APPLICABLE",
    "runtime_capture": "VISUAL_REQUIRED",
    "generative_mockup": "VISUAL_REQUIRED",
}

DOMAIN_PREFIXES = {
    "deliverables": "D",
    "user_directions": "UD",
    "acceptance_criteria": "AC",
    "tasks": "T",
    "affected_modules": "M",
    "planned_paths": "P",
    "actual_paths": "P",
    "runtime_surfaces": "RS",
}
REQUIRED_DOMAINS = (
    "deliverables",
    "user_directions",
    "acceptance_criteria",
    "tasks",
    "affected_modules",
)
NONVISUAL_KINDS = {
    "backend",
    "cli",
    "library",
    "validator",
    "workflow",
    "automation",
    "ci",
    "infrastructure",
    "migration",
    "data_contract",
    "security",
    "observability",
    "process_documentation",
    "docs_mermaid",
    "nonvisual",
}
RUNTIME_KINDS = {
    "ui_copy",
    "aria",
    "focus_behavior",
    "css_regression",
    "existing_component_state",
    "existing_visual_behavior",
    "frontend_affecting_contract",
    "user_visible_interface",
}
GENERATIVE_KINDS = {
    "new_screen",
    "new_component",
    "new_visual_concept",
    "generated_web_asset",
    "redesign",
    "marketing_asset",
    "inherently_visual",
}
VALID_KINDS = NONVISUAL_KINDS | RUNTIME_KINDS | GENERATIVE_KINDS
AUTHORITY_RANK = {
    "repository": 10,
    "plan": 20,
    "acceptance": 30,
    "user": 40,
    "system": 50,
}


def canonical_sha256(value: Any) -> str:
    """Hash JSON canonically, or text after transport-only newline normalization."""

    if isinstance(value, str):
        encoded = value.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    else:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def markdown_section(body: str, heading: str) -> str:
    match = re.search(
        rf"(?ims)^##[ \t]+{re.escape(heading)}[ \t]*$\n(.*?)(?=^##[ \t]+|\Z)",
        body,
    )
    return match.group(1).strip() if match else ""


def _sequential(ids: list[str], prefix: str) -> bool:
    return ids == [f"{prefix}-{index:03d}" for index in range(1, len(ids) + 1)]


def _intent_group(text: str) -> str:
    lowered = text.lower()
    if re.search(
        r"\b(visual[- ]applicability|evidence[_ -]mode|generative_mockup|"
        r"classif(?:y|ication)|selects? (?:the )?(?:visual|runtime|generative))\b",
        lowered,
    ):
        return "nonvisual"
    return _inferred_kind_group({"source": text}) or "nonvisual"


def _direction_entry(identifier: str, source: str, order: int) -> dict[str, Any]:
    lowered = source.lower()
    if re.search(
        r"\b(?:no|avoid|skip|without|do not|should not|when not)\b"
        r".{0,80}\b(?:imagegen|images?|mockups?)\b",
        lowered,
    ) or re.search(
        r"\b(?:imagegen|images?|mockups?)\b.{0,80}\b(?:do not|should not)\b",
        lowered,
    ):
        directive = "suppress"
    elif re.search(
        r"\b(?:request|generate|create|use)\b.{0,40}\b(?:imagegen|visual exploration)\b",
        lowered,
    ):
        directive = "request"
    else:
        directive = "neutral"
    return {
        "id": identifier,
        "kind": "nonvisual",
        "source": source,
        "source_sha256": canonical_sha256(source),
        "provenance": "persisted user direction",
        "directive": directive,
        "authority": "user",
        "scope": "D-001",
        "source_order": order,
        "turn": str(order),
    }


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

    task_lines = re.findall(r"(?im)^[ \t]*[-*][ \t]+\[[ xX]\][ \t]+.+$", tasks)
    task_ids: list[str] = []
    task_entries: list[dict[str, str]] = []
    modules: list[dict[str, str]] = []
    for line in task_lines:
        task_match = re.search(r"\b(T-\d{3})\b", line)
        if task_match is None:
            errors.append("every task must contain a stable T-NNN marker")
            continue
        task_id = task_match.group(1)
        task_ids.append(task_id)
        modules_match = re.search(
            r"Affected modules:\s*(.*?)\.\s+Requirements:", line, re.IGNORECASE
        )
        if modules_match is None:
            errors.append(f"{task_id} lacks a parseable Affected modules clause")
            continue
        entries = [entry.strip() for entry in modules_match.group(1).split(",") if entry.strip()]
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
        intent_match = re.match(
            r"(?i)^[ \t]*[-*][ \t]+\[[ xX]\][ \t]+\*\*T-\d{3}[^\n]*?\*\*"
            r"(.*?)(?=Affected modules:)",
            line,
        )
        task_intent = (
            re.sub(r"[*`]", "", line[: modules_match.start()])
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
                "source": line.strip(),
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
            }
        )
    inventory["planned_paths"] = planned_paths
    return inventory, errors


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _inferred_kind_group(entry: dict[str, Any]) -> str | None:
    text = " ".join(
        str(entry.get(field, ""))
        for field in ("source", "path", "provenance")
    ).lower()
    if re.search(
        r"\b(new[ _.-]?screen|new page|new component|new visual concept|redesign|"
        r"marketing asset|generated (?:web )?asset|landing page|hero image)\b",
        text,
    ):
        return "generative"
    if re.search(
        r"\b(aria|focus behavior|css regression|ui copy|existing component|"
        r"existing visual|responsive|visual accessibility|user-visible interface)\b",
        text,
    ):
        return "runtime"
    path = str(entry.get("path") or entry.get("source") or "").lower().strip("` ")
    if re.search(r"(?:^|/)(?:web|ui|frontend|mobile|desktop|components?)/", path) or re.search(
        r"\.(?:tsx|jsx|vue|svelte|css|scss|sass|less)$", path
    ):
        return "runtime"
    if re.search(
        r"(?:^|/)(?:scripts?|tests?|references?|bundled-skills?|infra|migrations?|"
        r"\.github/workflows?)/",
        path,
    ) or re.search(r"\.(?:py|sh|bash|json|ya?ml|toml|sql|md)$", path):
        return "nonvisual"
    if re.search(
        r"\b(backend|cli|library|validator|verifier|workflow|automation|ci/cd|"
        r"infrastructure|migration|data contract|security rule|observability|"
        r"process documentation|mermaid|nonvisual|tests?|fixtures?|references?|"
        r"helpers?|readme|contracts?|schemas?|packaging|notices?|provenance|"
        r"serializers?|scripts?)\b",
        text,
    ):
        return "nonvisual"
    return None


def _declared_kind_group(kind: Any) -> str | None:
    if kind in NONVISUAL_KINDS:
        return "nonvisual"
    if kind in RUNTIME_KINDS:
        return "runtime"
    if kind in GENERATIVE_KINDS:
        return "generative"
    return None


def _entry_errors(inventory: dict[str, Any], declared_ids: dict[str, Any] | None) -> list[str]:
    errors: list[str] = []
    for domain in REQUIRED_DOMAINS:
        entries = inventory.get(domain)
        prefix = DOMAIN_PREFIXES[domain]
        if not isinstance(entries, list) or not entries:
            errors.append(f"scope inventory domain {domain} must be a nonempty array")
            continue
        ids = [entry.get("id") for entry in entries if isinstance(entry, dict)]
        if len(ids) != len(entries) or len(ids) != len(set(ids)):
            errors.append(f"scope inventory domain {domain} has omitted or duplicate IDs")
        elif not _sequential(ids, prefix):
            errors.append(f"scope inventory domain {domain} IDs must be sequential {prefix}-NNN")
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("kind") not in VALID_KINDS:
                errors.append(f"{entry.get('id', domain)} has unknown or missing visual kind")
            elif entry.get("kind") in RUNTIME_KINDS and not isinstance(
                entry.get("runtime_evidence_sufficient"), bool
            ):
                errors.append(
                    f"{entry.get('id', domain)} lacks a runtime-evidence sufficiency decision"
                )
            if not _nonempty_text(entry.get("provenance")):
                errors.append(f"{entry.get('id', domain)} lacks provenance")
            if domain != "user_directions":
                if not _nonempty_text(entry.get("source")):
                    errors.append(f"{entry.get('id', domain)} lacks authoritative source text")
                inferred = _inferred_kind_group(entry)
                declared_group = _declared_kind_group(entry.get("kind"))
                if entry.get("classification_basis") == "affected_modules":
                    inferred = declared_group
                if inferred is None:
                    errors.append(
                        f"{entry.get('id', domain)} cannot be independently classified"
                    )
                elif inferred != declared_group:
                    errors.append(
                        f"{entry.get('id', domain)} declared {declared_group} but source implies {inferred}"
                    )
        if declared_ids is not None:
            declared = declared_ids.get(domain)
            if not isinstance(declared, list) or declared != ids:
                errors.append(f"scope inventory exact-set mismatch for {domain}")

    for path_domain in ("planned_paths", "actual_paths", "runtime_surfaces"):
        if path_domain not in inventory:
            continue
        entries = inventory[path_domain]
        if not isinstance(entries, list):
            errors.append(f"scope inventory domain {path_domain} must be an array")
        else:
            prefix = DOMAIN_PREFIXES[path_domain]
            ids = [entry.get("id") for entry in entries if isinstance(entry, dict)]
            if len(ids) != len(entries) or len(ids) != len(set(ids)) or not _sequential(
                ids, prefix
            ):
                errors.append(f"scope inventory domain {path_domain} IDs must be exact and sequential")
            if declared_ids is not None and declared_ids.get(path_domain) != ids:
                errors.append(f"scope inventory exact-set mismatch for {path_domain}")
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("kind") not in VALID_KINDS:
                    errors.append(f"{path_domain} contains an unclassified path")
                elif entry.get("kind") in RUNTIME_KINDS and not isinstance(
                    entry.get("runtime_evidence_sufficient"), bool
                ):
                    errors.append(
                        f"{entry.get('id', path_domain)} lacks a runtime-evidence sufficiency decision"
                    )
                elif not _nonempty_text(entry.get("path")) or not _nonempty_text(
                    entry.get("provenance")
                ):
                    errors.append(f"{entry.get('id', path_domain)} is masked or lacks provenance")
                else:
                    inferred = _inferred_kind_group(entry)
                    declared_group = _declared_kind_group(entry.get("kind"))
                    if inferred is None:
                        errors.append(
                            f"{entry.get('id', path_domain)} cannot be independently classified"
                        )
                    elif inferred != declared_group:
                        errors.append(
                            f"{entry.get('id', path_domain)} declared {declared_group} but path/source implies {inferred}"
                        )
    return errors


def _authority(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return AUTHORITY_RANK.get(str(value).lower(), -1)


def resolve_directions(
    directions: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve opt-in/opt-out directions by authority, scope, turn, and source order."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for direction in directions:
        directive = direction.get("directive")
        if directive not in {"request", "suppress", "neutral"}:
            errors.append(f"{direction.get('id', 'direction')} has invalid directive")
            continue
        scope = direction.get("scope")
        order = direction.get("source_order")
        if not _nonempty_text(scope) or not isinstance(order, int):
            errors.append(f"{direction.get('id', 'direction')} lacks scope or source order")
            continue
        rank = _authority(direction.get("authority"))
        if rank < 0:
            errors.append(f"{direction.get('id', 'direction')} has unknown authority")
            continue
        normalized = dict(direction)
        normalized["_authority_rank"] = rank
        grouped.setdefault(scope, []).append(normalized)

    effective: list[dict[str, Any]] = []
    for scope in sorted(grouped):
        candidates = grouped[scope]
        max_rank = max(item["_authority_rank"] for item in candidates)
        peers = [item for item in candidates if item["_authority_rank"] == max_rank]
        by_turn: dict[str, set[str]] = {}
        for item in peers:
            if item["directive"] != "neutral":
                by_turn.setdefault(str(item.get("turn", "")), set()).add(item["directive"])
        if any(len(directives) > 1 for directives in by_turn.values()):
            errors.append(f"unresolved visual direction conflict for scope {scope}")
            continue
        max_order = max(item["source_order"] for item in peers)
        newest = [item for item in peers if item["source_order"] == max_order]
        directives = {item["directive"] for item in newest if item["directive"] != "neutral"}
        if len(directives) > 1:
            errors.append(f"unresolved visual direction conflict for scope {scope}")
            continue
        winner = sorted(newest, key=lambda item: str(item.get("id", "")))[-1]
        winner.pop("_authority_rank", None)
        effective.append(winner)
    return effective, errors


def evaluate_visual_applicability(
    inventory: dict[str, Any],
    *,
    phase: str,
    authoritative_issue_body: str,
    declared_ids: dict[str, Any] | None = None,
    repository_signals: list[dict[str, Any]] | None = None,
    material_ambiguities: list[str] | None = None,
    nonmaterial_uncertainties: list[str] | None = None,
) -> dict[str, Any]:
    """Recompute a phase-bound visual disposition from complete scoped evidence."""

    errors = _entry_errors(inventory, declared_ids)
    if declared_ids is None:
        errors.append("visual applicability requires declared exact-set scope IDs")
    if phase == "review" and not inventory.get("actual_paths"):
        errors.append("Review visual applicability requires complete actual changed paths")
    if phase in {"plan", "implement-orientation", "implement"} and not inventory.get(
        "planned_paths"
    ):
        errors.append(f"{phase} visual applicability requires complete intended changed paths")
    directions, direction_errors = resolve_directions(inventory.get("user_directions", []))
    errors.extend(direction_errors)
    material = [value for value in material_ambiguities or [] if _nonempty_text(value)]
    uncertainty = [value for value in nonmaterial_uncertainties or [] if _nonempty_text(value)]

    entries: list[dict[str, Any]] = []
    for domain, values in inventory.items():
        if domain in DOMAIN_PREFIXES and domain != "user_directions" and isinstance(values, list):
            entries.extend(value for value in values if isinstance(value, dict))

    runtime = [entry for entry in entries if entry.get("kind") in RUNTIME_KINDS]
    runtime_needing_concept = [
        entry for entry in runtime if entry.get("runtime_evidence_sufficient") is False
    ]
    generative = [entry for entry in entries if entry.get("kind") in GENERATIVE_KINDS]
    requests = [item for item in directions if item.get("directive") == "request"]
    suppressions = [item for item in directions if item.get("directive") == "suppress"]

    acceptance_requires_generation = any(
        entry.get("kind") in GENERATIVE_KINDS
        or (
            entry.get("kind") in RUNTIME_KINDS
            and entry.get("runtime_evidence_sufficient") is False
        )
        for entry in inventory.get("acceptance_criteria", [])
        if isinstance(entry, dict)
    )
    blocked_reasons = list(errors) + material
    if suppressions and (acceptance_requires_generation or generative):
        blocked_reasons.append(
            "effective no-ImageGen direction cannot waive acceptance-critical visual evidence"
        )

    if blocked_reasons:
        mode: str | None = None
        decision = BLOCKED_DECISION
        status = "blocked"
    elif requests or generative or runtime_needing_concept:
        mode = "generative_mockup"
        decision = DECISION_BY_MODE[mode]
        status = "resolved"
    elif runtime:
        mode = "runtime_capture"
        decision = DECISION_BY_MODE[mode]
        status = "resolved"
    else:
        mode = "none"
        decision = DECISION_BY_MODE[mode]
        status = "resolved"

    # Repository signals are diagnostic only. Scoped inventory entries decide.
    ignored_signals = []
    scoped_paths = {
        entry.get("path")
        for domain in ("planned_paths", "actual_paths")
        for entry in inventory.get(domain, [])
        if isinstance(entry, dict)
    }
    for signal in repository_signals or []:
        if signal.get("path") not in scoped_paths:
            ignored_signals.append(signal)

    triggers = [
        {"id": entry.get("id"), "kind": entry.get("kind"), "provenance": entry.get("provenance")}
        for entry in generative + runtime
    ]
    triggers.extend(
        {"id": item.get("id"), "kind": "explicit_visual_request", "provenance": item.get("provenance")}
        for item in requests
    )
    counts = {
        domain: len(values)
        for domain, values in inventory.items()
        if domain in DOMAIN_PREFIXES and isinstance(values, list)
    }
    components = [
        str(entry.get("path") or entry.get("source") or entry.get("id"))
        for entry in entries
    ]
    return {
        "policy_version": POLICY_VERSION,
        "status": status,
        "decision": decision,
        "evidence_mode": mode,
        "phase_binding": {
            "phase": phase,
            "authoritative_issue_body_sha256": canonical_sha256(authoritative_issue_body),
        },
        "scope_inventory_sha256": inventory_sha256(inventory),
        "scope_inventory": inventory,
        "declared_scope_ids": declared_ids,
        "scope_inventory_counts": counts,
        "scope_inventory_status": "complete" if not errors else "incomplete",
        "effective_directions": directions,
        "matched_triggers": triggers,
        "blocking_reasons": blocked_reasons,
        "uncertainty": uncertainty,
        "ignored_repository_signals": ignored_signals,
        "evidence": sorted(
            {
                str(entry.get("provenance"))
                for entry in entries
                if _nonempty_text(entry.get("provenance"))
            }
        ),
        "scoped_components": components,
    }


def validate_disposition(
    receipt: Any,
    body: str,
    *,
    phase: str = "plan",
    inventory: dict[str, Any] | None = None,
    declared_ids: dict[str, Any] | None = None,
    repository_signals: list[dict[str, Any]] | None = None,
    material_ambiguities: list[str] | None = None,
    nonmaterial_uncertainties: list[str] | None = None,
    authoritative_paths: list[str] | None = None,
    require_embedded_inventory: bool = False,
    authoritative_user_directions: list[str] | None = None,
) -> tuple[str | None, dict[str, Any] | None, list[str]]:
    """Validate a receipt and, when supplied, recompute it from current evidence."""

    errors: list[str] = []
    if not isinstance(receipt, dict):
        return None, None, ["visual_artifact_disposition must be an object"]
    if receipt.get("policy_version") != POLICY_VERSION:
        errors.append(f"visual_artifact_disposition.policy_version must be {POLICY_VERSION}")
    mode = receipt.get("evidence_mode")
    if receipt.get("status") == "blocked":
        if receipt.get("decision") != BLOCKED_DECISION or mode is not None:
            errors.append("blocked visual disposition has an invalid decision or evidence mode")
    elif mode not in DECISION_BY_MODE:
        errors.append("visual_artifact_disposition.evidence_mode is invalid")
        mode = None
    elif receipt.get("decision") != DECISION_BY_MODE[mode]:
        errors.append("visual_artifact_disposition decision does not match evidence_mode")

    for field in ("evidence", "scoped_components"):
        values = receipt.get(field)
        if not isinstance(values, list) or not any(_nonempty_text(value) for value in values):
            errors.append(f"visual_artifact_disposition.{field} must be nonempty")
    for field in ("matched_triggers", "uncertainty"):
        if not isinstance(receipt.get(field), list):
            errors.append(f"visual_artifact_disposition.{field} must be an array")
    if "status" in receipt and not isinstance(receipt.get("blocking_reasons"), list):
        errors.append("visual_artifact_disposition.blocking_reasons must be an array")

    binding = receipt.get("phase_binding")
    if not isinstance(binding, dict):
        errors.append("visual_artifact_disposition.phase_binding must be an object")
    else:
        if binding.get("phase") != phase:
            errors.append("visual_artifact_disposition phase binding is stale")
        if binding.get("authoritative_issue_body_sha256") != canonical_sha256(body):
            errors.append("visual disposition is not bound to the authoritative issue body")

    recompute = inventory is not None
    embedded_inventory = (
        receipt.get("scope_inventory")
        if isinstance(receipt.get("scope_inventory"), dict)
        else None
    )
    if require_embedded_inventory and embedded_inventory is None:
        errors.append(
            "v2 visual disposition requires an embedded authoritative scope inventory"
        )
    if require_embedded_inventory and not authoritative_user_directions:
        errors.append(
            "v2 visual disposition requires persisted authoritative user directions"
        )
    if inventory is None and isinstance(receipt.get("scope_inventory"), dict):
        inventory = receipt["scope_inventory"]
        if declared_ids is None and isinstance(receipt.get("declared_scope_ids"), dict):
            declared_ids = receipt["declared_scope_ids"]
        recompute = True
    if inventory is None:
        inventory, inventory_errors = extract_scope_inventory(
            body, user_directions=authoritative_user_directions
        )
        errors.extend(inventory_errors)
    else:
        errors.extend(_entry_errors(inventory, declared_ids))
    authoritative_inventory, authoritative_errors = extract_scope_inventory(
        body, user_directions=authoritative_user_directions
    )
    errors.extend(authoritative_errors)
    if not authoritative_errors and embedded_inventory is not None:
        for domain in (
            "user_directions",
            "acceptance_criteria",
            "tasks",
            "affected_modules",
        ):
            recorded = embedded_inventory.get(domain)
            authoritative = authoritative_inventory.get(domain)
            if not isinstance(recorded, list) or not isinstance(authoritative, list):
                errors.append(f"visual authoritative binding is missing {domain}")
                continue
            if domain == "user_directions":
                semantic_fields = (
                    "id",
                    "kind",
                    "source",
                    "source_sha256",
                    "provenance",
                    "directive",
                    "authority",
                    "scope",
                    "source_order",
                    "turn",
                )
                recorded_semantics = [
                    {field: entry.get(field) for field in semantic_fields}
                    for entry in recorded
                    if isinstance(entry, dict)
                ]
                authoritative_semantics = [
                    {field: entry.get(field) for field in semantic_fields}
                    for entry in authoritative
                    if isinstance(entry, dict)
                ]
                if recorded_semantics != authoritative_semantics:
                    errors.append(
                        "visual user_directions do not exactly match persisted "
                        "authoritative direction semantics"
                    )
                continue
            recorded_rows = [
                (
                    entry.get("id"),
                    str(entry.get("source", "")).strip(),
                    _declared_kind_group(entry.get("kind")),
                )
                for entry in recorded
                if isinstance(entry, dict)
            ]
            authoritative_rows = [
                (
                    entry.get("id"),
                    str(entry.get("source", "")).strip(),
                    _declared_kind_group(entry.get("kind")),
                )
                for entry in authoritative
                if isinstance(entry, dict)
            ]
            if recorded_rows != authoritative_rows:
                errors.append(
                    f"visual {domain} do not exactly match authoritative issue text"
                )
    if receipt.get("scope_inventory_sha256") != inventory_sha256(inventory):
        errors.append("visual scope inventory SHA-256 does not match current scope evidence")
    if authoritative_paths is not None:
        path_domain = "actual_paths" if phase == "review" else "planned_paths"
        recorded_paths = sorted(
            str(entry.get("path"))
            for entry in inventory.get(path_domain, [])
            if isinstance(entry, dict) and _nonempty_text(entry.get("path"))
        )
        if recorded_paths != sorted(set(authoritative_paths)):
            errors.append(
                f"visual {path_domain} do not match independently derived repository paths"
            )

    if recompute and declared_ids is not None:
        recomputed = evaluate_visual_applicability(
            inventory,
            phase=phase,
            authoritative_issue_body=body,
            declared_ids=declared_ids,
            repository_signals=repository_signals,
            material_ambiguities=material_ambiguities,
            nonmaterial_uncertainties=nonmaterial_uncertainties,
        )
        compared_fields = (
            "status",
            "decision",
            "evidence_mode",
            "scope_inventory_sha256",
            "scope_inventory_counts",
            "scope_inventory_status",
            "effective_directions",
            "matched_triggers",
            "blocking_reasons",
            "uncertainty",
        )
        for field in compared_fields:
            if receipt.get(field) != recomputed.get(field):
                errors.append(f"visual disposition field {field} does not match recomputation")

    if mode == "none":
        if receipt.get("matched_triggers") != []:
            errors.append("visual mode none cannot contain matched visual triggers")
        if receipt.get("scope_inventory_status") != "complete":
            errors.append("visual mode none requires complete positive nonvisual coverage")
    return mode, inventory, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("body", type=Path)
    parser.add_argument("--phase", default="plan")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--user-direction",
        action="append",
        required=True,
        help="Persisted effective user direction; repeat in source order",
    )
    args = parser.parse_args()
    try:
        body = args.body.read_text(encoding="utf-8")
        inventory, extraction_errors = build_plan_inventory(
            body, user_directions=args.user_direction
        )
        declared = {
            domain: [entry["id"] for entry in entries]
            for domain, entries in inventory.items()
            if domain in DOMAIN_PREFIXES and isinstance(entries, list)
        }
        receipt = evaluate_visual_applicability(
            inventory,
            phase=args.phase,
            authoritative_issue_body=body,
            declared_ids=declared,
        )
        if extraction_errors:
            receipt["blocking_reasons"] = sorted(
                set(receipt["blocking_reasons"] + extraction_errors)
            )
            receipt["status"] = "blocked"
            receipt["decision"] = BLOCKED_DECISION
            receipt["evidence_mode"] = None
        rendered = json.dumps(receipt, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0 if receipt["status"] == "resolved" else 1
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
