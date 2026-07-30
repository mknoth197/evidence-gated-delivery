#!/usr/bin/env python3
"""Visual applicability policy evaluation and receipt verification."""
from __future__ import annotations

import copy
import re
from typing import Any

from visual_core import (
    AUTHORITY_RANK, BLOCKED_DECISION, DECISION_BY_MODE, DOMAIN_PREFIXES,
    GENERATIVE_KINDS, NONVISUAL_KINDS, POLICY_VERSION, REQUIRED_DOMAINS,
    RUNTIME_KINDS, VALID_KINDS, canonical_sha256, markdown_section,
    _declared_kind_group, _inferred_kind_group, _intent_group, _nonempty_text,
    _sequential,
)
from visual_inventory import extract_scope_inventory, inventory_sha256

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
    authoritative_runtime_evidence: list[dict[str, Any]] | None = None,
    runtime_evidence_not_before: str | None = None,
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
            body,
            user_directions=authoritative_user_directions,
            runtime_evidence=authoritative_runtime_evidence,
            runtime_evidence_not_before=runtime_evidence_not_before,
        )
        errors.extend(inventory_errors)
    else:
        errors.extend(_entry_errors(inventory, declared_ids))
    authoritative_inventory, authoritative_errors = extract_scope_inventory(
        body,
        user_directions=authoritative_user_directions,
        runtime_evidence=authoritative_runtime_evidence,
        runtime_evidence_not_before=runtime_evidence_not_before,
    )
    errors.extend(authoritative_errors)
    if not authoritative_errors and embedded_inventory is not None:
        for domain in (
            "deliverables",
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
                    entry.get("runtime_evidence_sufficient"),
                )
                for entry in recorded
                if isinstance(entry, dict)
            ]
            authoritative_rows = [
                (
                    entry.get("id"),
                    str(entry.get("source", "")).strip(),
                    _declared_kind_group(entry.get("kind")),
                    entry.get("runtime_evidence_sufficient"),
                )
                for entry in authoritative
                if isinstance(entry, dict)
            ]
            if recorded_rows != authoritative_rows:
                errors.append(
                    f"visual {domain} do not exactly match authoritative issue "
                    "text and runtime evidence"
                )
    if authoritative_runtime_evidence is not None and not authoritative_errors:
        inventory = authoritative_inventory
        declared_ids = {
            domain: [
                entry.get("id")
                for entry in values
                if isinstance(entry, dict) and _nonempty_text(entry.get("id"))
            ]
            for domain, values in authoritative_inventory.items()
            if domain in DOMAIN_PREFIXES and isinstance(values, list)
        }
        recompute = True
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
