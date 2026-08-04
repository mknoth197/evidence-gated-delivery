#!/usr/bin/env python3
"""Compose phase validators with authenticated predecessor evidence."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable

try:  # Support both `scripts.*` imports and direct CLI execution.
    from . import predecessor_evidence
except ImportError:  # pragma: no cover - exercised by validate_run.py CLI imports
    import predecessor_evidence


@dataclass(frozen=True)
class PhaseValidationDependencies:
    nonempty: Callable[..., bool]
    github_readback: Callable[..., Any]
    validate_timeline: Callable[..., None]
    validate_trace_audit: Callable[..., None]
    add_research_errors: Callable[..., Any]
    add_plan_errors: Callable[..., None]
    add_orientation_errors: Callable[..., None]
    add_implement_errors: Callable[..., None]
    review_changed_paths: Callable[..., Any]
    validate_retrospective_gate: Callable[..., None]
    validate_transition_gate: Callable[..., None]
    add_handoff_error: Callable[..., None]
    mode_by_phase: dict[str, set[str]]


def effective_timeline(
    timeline: dict[str, Any],
    predecessor_data: dict[str, Any] | None,
    predecessor_binding: dict[str, Any] | None,
    *,
    nonempty: Callable[[Any], bool],
    timestamp: Callable[[Any], Any],
) -> tuple[dict[str, Any], list[str]]:
    """Project authenticated predecessor times without copying them into a successor."""
    projected: dict[str, Any] = {}
    errors: list[str] = []
    if isinstance(predecessor_data, dict) and isinstance(
        predecessor_data.get("phase_timeline"), dict
    ):
        projected.update(predecessor_data["phase_timeline"])
    projected.update({key: value for key, value in timeline.items() if nonempty(value)})
    if isinstance(predecessor_binding, dict):
        phase = predecessor_binding.get("phase")
        validated_at = predecessor_binding.get("validated_at")
        if isinstance(phase, str) and timestamp(validated_at) is not None:
            field = f"{phase}_completed_at"
            explicit = timestamp(timeline.get(field))
            if explicit is not None and explicit != timestamp(validated_at):
                errors.append(f"phase_timeline.{field} conflicts with imported predecessor receipt")
            projected[field] = validated_at
    return projected, errors


def _live_authority_reader(
    raw: dict[str, Any], skip_remote: bool, deps: PhaseValidationDependencies
) -> Callable[[str], str]:
    def read(url: str) -> str:
        if skip_remote:
            authority = raw.get("authority")
            value = authority.get("implementation_issue_body_sha256") if isinstance(authority, dict) else None
            if isinstance(value, str):
                return value
            raise ValueError("predecessor authority SHA-256 is unavailable")
        body, error = deps.github_readback(url, "issue")
        if error is not None or body is None:
            raise ValueError(error or "remote authority body is unavailable")
        return hashlib.sha256(body.encode()).hexdigest()
    return read


def _load(
    data: dict[str, Any],
    expected_phase: str,
    errors: list[str],
    skip_remote: bool,
    deps: PhaseValidationDependencies,
    ancestor_receipt_sha256s: tuple[str, ...] = (),
) -> predecessor_evidence.PredecessorEvidenceResult | None:
    raw = data.get("predecessor_evidence")
    if not isinstance(raw, dict) or not raw:
        return None
    judgments = data.get("phase_transition_judgments")
    trusted_receipt_sha256s = {
        entry.get("phase_receipt_sha256")
        for entry in judgments or []
        if isinstance(entry, dict)
        and entry.get("phase") == expected_phase
        and isinstance(entry.get("phase_receipt_sha256"), str)
    }
    result = predecessor_evidence.validate_predecessor_evidence(
        data,
        expected_phase,
        live_authority_sha256=_live_authority_reader(raw, skip_remote, deps),
        ancestor_receipt_sha256s=ancestor_receipt_sha256s,
        trusted_receipt_sha256s=trusted_receipt_sha256s,
    )
    errors.extend(result.errors)
    return result


def _projection(
    direct: predecessor_evidence.PredecessorEvidenceResult,
    plan: predecessor_evidence.PredecessorEvidenceResult | None = None,
) -> dict[str, Any]:
    projection: dict[str, Any] = {
        "phase_timeline": {},
        "phase_receipt_bindings": {},
        "phase_retrospectives": [],
    }
    role_lists = (
        "contestants", "judges", "implementation_workers", "trace_audits",
        "plan_audits", "phase_transition_judgments",
    )
    for field in role_lists:
        projection[field] = []
    retrospectives: dict[str, dict[str, Any]] = {}
    for result in (value for value in (plan, direct) if value is not None and value.valid):
        assert result.manifest is not None
        timeline = result.manifest.get("phase_timeline")
        if isinstance(timeline, dict):
            projection["phase_timeline"].update(
                {key: value for key, value in timeline.items() if value not in (None, "")}
            )
        bindings = result.manifest.get("phase_receipt_bindings")
        if isinstance(bindings, dict):
            projection["phase_receipt_bindings"].update(bindings)
        entries = result.manifest.get("phase_retrospectives")
        if isinstance(entries, list):
            retrospectives.update(
                {entry["phase"]: entry for entry in entries if isinstance(entry, dict) and isinstance(entry.get("phase"), str)}
            )
        for field in role_lists:
            values = result.manifest.get(field)
            if isinstance(values, list):
                projection[field].extend(values)
        for field in ("test_reviewer", "acceptance_reviewer"):
            value = result.manifest.get(field)
            if isinstance(value, dict) and value:
                projection[field] = value
        binding = dict(result.binding or {})
        imported_phase = binding.get("phase")
        if isinstance(imported_phase, str):
            projection["phase_receipt_bindings"][imported_phase] = binding
            projection["phase_timeline"][f"{imported_phase}_completed_at"] = binding.get("validated_at")
    projection["phase_retrospectives"] = list(retrospectives.values())
    return projection


def validate(
    data: dict[str, Any], phase: str, skip_remote: bool, *, deps: PhaseValidationDependencies
) -> list[str]:
    errors: list[str] = []
    for field in ("run_id", "parent_thread_id", "mode", "goal", "selected_mode_reason", "workflow_version"):
        if not deps.nonempty(data.get(field)):
            errors.append(f"{field} is required")
    if data.get("mode") not in deps.mode_by_phase[phase]:
        errors.append(f"mode {data.get('mode')!r} cannot validate phase {phase}")

    direct = None
    plan = None
    expected = {"orchestrate-preapproval": "plan", "implement": "plan", "review": "implement"}.get(phase)
    if expected is not None:
        direct = _load(data, expected, errors, skip_remote, deps)
        if phase == "review" and direct is not None and direct.valid and direct.manifest is not None:
            receipt_sha = dict(direct.binding or {}).get("receipt_sha256")
            plan = _load(
                dict(direct.manifest), "plan", errors, skip_remote, deps,
                (receipt_sha,) if isinstance(receipt_sha, str) else (),
            )

    imported = direct is not None and direct.valid
    direct_manifest = dict(direct.manifest) if imported and direct.manifest is not None else None
    direct_binding = dict(direct.binding) if imported and direct.binding is not None else None
    plan_manifest = direct_manifest
    if phase == "review" and plan is not None and plan.valid and plan.manifest is not None:
        plan_manifest = dict(plan.manifest)
    projection = _projection(direct, plan) if imported else None

    deps.validate_timeline(
        data, phase, errors,
        predecessor_data=projection,
        predecessor_binding=direct_binding,
    )
    if not isinstance(data.get("phase_timeline"), dict):
        return errors
    deps.validate_trace_audit(data, phase, errors, prior_role_data=projection)

    if phase == "research":
        deps.add_research_errors(data, errors, skip_remote)
    elif phase == "plan":
        deps.add_plan_errors(data, errors, skip_remote)
    elif phase == "orchestrate-preapproval":
        deps.add_orientation_errors(data, errors, skip_remote, predecessor_plan=plan_manifest)
    elif phase == "implement":
        deps.add_implement_errors(data, errors, skip_remote, predecessor_plan=plan_manifest)
    elif phase == "review":
        paths = deps.review_changed_paths(data, errors)
        deps.add_implement_errors(
            direct_manifest or data, errors, skip_remote,
            visual_phase="review", review_paths=paths,
            predecessor_plan=plan_manifest if imported else None,
            successor_visual_data=data,
        )
        if data.get("review_dispositions_recorded") is not True:
            errors.append("review_dispositions_recorded must be true")
        if data.get("remote_checks_reported") is not True:
            errors.append("remote_checks_reported must be true")

    deps.validate_retrospective_gate(
        data, phase, errors,
        predecessor_data=projection,
        predecessor_binding=direct_binding,
    )
    if phase == "plan":
        deps.validate_transition_gate(data, "plan", errors)
    elif phase in {"orchestrate-preapproval", "implement"}:
        deps.validate_transition_gate(
            data, "implement", errors,
            predecessor_binding=direct_binding,
            prior_role_data=projection,
        )
    elif phase == "review":
        deps.validate_transition_gate(
            data, "review", errors,
            predecessor_binding=direct_binding,
            prior_role_data=projection,
        )
    deps.add_handoff_error(data, phase, errors)
    return errors
