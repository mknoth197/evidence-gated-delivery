#!/usr/bin/env python3
"""Phase-sensitive visual validation for authenticated predecessor Plans."""
from __future__ import annotations

from typing import Any


def validate(
    data: dict[str, Any],
    errors: list[str],
    skip_remote: bool,
    visual_phase: str,
    review_paths: list[str] | None = None,
    *,
    deps: Any,
) -> None:
    disposition = data.get("visual_artifact_disposition")
    visual_mode = (
        disposition.get("evidence_mode")
        if isinstance(disposition, dict)
        and disposition.get("evidence_mode")
        in {"none", "runtime_capture", "generative_mockup"}
        else None
    )
    if visual_mode is None:
        errors.append("visual_artifact_disposition.evidence_mode is invalid")
    implementation_body = deps.require_remote_issue(
        data.get("implementation_issue_url"),
        "implementation_issue_url",
        deps.PLAN_HEADINGS,
        errors,
        skip_remote,
    )
    if skip_remote or implementation_body is None:
        return
    runtime_upper_field = {
        "implement-orientation": "plan_completed_at",
        "implement": "implement_completed_at",
        "review": "review_completed_at",
    }.get(visual_phase)
    timeline = data.get("phase_timeline")
    validated_mode, _inventory, disposition_errors = deps.validate_disposition(
        disposition,
        implementation_body,
        phase=visual_phase,
        authoritative_paths=review_paths if visual_phase == "review" else None,
        require_embedded_inventory=data.get("plan_protocol_version") == "plan-protocol/v2",
        authoritative_user_directions=data.get("visual_user_directions"),
        authoritative_runtime_evidence=data.get("runtime_visual_evidence"),
        runtime_evidence_not_before=data.get("run_started_at"),
        runtime_evidence_not_after=(
            timeline.get(runtime_upper_field)
            if isinstance(timeline, dict) and runtime_upper_field
            else None
        ),
    )
    errors.extend(disposition_errors)
    if validated_mode is not None and visual_mode is not None and validated_mode != visual_mode:
        errors.append("visual mode changed during successor phase validation")
    if visual_mode == "runtime_capture":
        runtime_evidence = data.get("runtime_visual_evidence")
        if not isinstance(runtime_evidence, list) or not any(
            isinstance(item, dict)
            and deps.nonempty(item.get("kind"))
            and deps.nonempty(item.get("evidence"))
            for item in runtime_evidence
        ):
            errors.append("runtime_capture requires current runtime visual evidence")
