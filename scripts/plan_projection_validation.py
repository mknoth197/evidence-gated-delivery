#!/usr/bin/env python3
"""Assemble and replay the six immutable Plan projection adapters."""
from __future__ import annotations

import copy
import re
from typing import Any

from plan_protocol import PlanProtocolError, issue_body_sha256
from plan_tasks import (
    graph_policy_from_projection_bundle,
    graph_policy_projection_adapter,
    task_projection_adapter,
    tasks_from_projection_bundle,
)
from plan_graph import graph_draft_from_projection_bundle, graph_draft_projection_adapter
from visual_policy import (
    visual_disposition_from_projection_bundle,
    visual_disposition_projection_adapter,
)
from plan_audits import (
    plan_audit_inputs_projection_adapter,
    validate_plan_audits_from_projection_bundle,
)
from preflight_plan import preflight_from_projection_bundle, preflight_projection_adapter
from projection_bundle import (
    PROJECTION_KERNEL_VERSION,
    projection_sha256,
    validate_projection_bundle,
    validate_projection_transaction_receipt,
)
from projection_kernel import run_projection_kernel


PLAN_PROJECTION_REQUIRED_SLOTS = (
    "tasks",
    "graph_policy",
    "graph_draft",
    "visual_disposition",
    "plan_audit_inputs",
    "preflight",
)


def _disallowed_agent_ids(data: dict[str, Any]) -> list[str]:
    values: set[str] = set()
    for field in (
        "contestants",
        "judges",
        "trace_audits",
        "implementation_workers",
        "phase_retrospectives",
        "phase_transition_judgments",
    ):
        entries = data.get(field)
        if isinstance(entries, list):
            values.update(
                entry["agent_id"]
                for entry in entries
                if isinstance(entry, dict)
                and isinstance(entry.get("agent_id"), str)
                and entry["agent_id"].strip()
            )
    return sorted(values)


def assemble_plan_projection_transaction_evidence(
    data: dict[str, Any],
    implementation_body_bytes: bytes,
    *,
    prepared_at: str,
    completed_at: str,
    intent: dict[str, Any] | None = None,
    disallowed_agent_ids: list[str] | None = None,
    audit_receipts: list[Any] | None = None,
    provider_receipts: list[Any] | None = None,
    graph_operations: list[Any] | None = None,
    gate_outcomes: list[Any] | None = None,
    external_actions: list[Any] | None = None,
    parent_bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prepare six Plan projections from one immutable authority read.

    Persist the result atomically through ``projection_kernel``. Graph mutation
    remains later: call ``stage_graph_external_intent`` and pass the unchanged
    bundle plus staged intent to ``execute_transaction``. Append normalized
    read-back to transaction evidence; never rebuild the prepared bundle.
    """

    if not isinstance(implementation_body_bytes, bytes):
        raise PlanProtocolError("implementation body authority must be immutable bytes")
    try:
        body = implementation_body_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PlanProtocolError("implementation body authority must be valid UTF-8") from exc
    issue_url = str(data.get("implementation_issue_url") or "")
    repository_match = re.fullmatch(
        r"https://github\.com/([^/]+/[^/]+)/issues/\d+", issue_url
    )
    if repository_match is None:
        raise PlanProtocolError("implementation_issue_url must identify an exact GitHub issue")
    capsule = data.get("context_capsule_ref")
    if not isinstance(capsule, dict):
        raise PlanProtocolError("context_capsule_ref is required for projection preparation")
    effective = data.get("effective_assurance") or "heavy"
    assurance = {
        "requested": data.get("requested_assurance")
        or data.get("requested_legacy_tier")
        or effective,
        "effective": effective,
        "selection_origin": data.get("selection_origin") or "legacy_phase_command",
        "legacy_subprofile": data.get("legacy_subprofile"),
    }
    evaluated_at = (
        data.get("graph_policy_receipt", {}).get("evaluated_at")
        if isinstance(data.get("graph_policy_receipt"), dict)
        else None
    ) or prepared_at
    directions = list(data.get("visual_user_directions") or [])
    runtime = copy.deepcopy(data.get("runtime_visual_evidence") or [])
    adapters = {
        "tasks": task_projection_adapter,
        "graph_policy": graph_policy_projection_adapter(evaluated_at=evaluated_at),
        "graph_draft": graph_draft_projection_adapter(
            parent_issue_url=issue_url, repository=repository_match.group(1)
        ),
        "visual_disposition": visual_disposition_projection_adapter(
            phase="plan",
            user_directions=directions,
            runtime_evidence=runtime,
            runtime_evidence_not_before=data.get("run_started_at"),
            runtime_evidence_not_after=(
                data.get("phase_timeline", {}).get("plan_completed_at")
                if isinstance(data.get("phase_timeline"), dict)
                else None
            ),
        ),
        "plan_audit_inputs": plan_audit_inputs_projection_adapter(
            audits=copy.deepcopy(data.get("plan_audits") or []),
            disallowed_agent_ids=(
                disallowed_agent_ids
                if disallowed_agent_ids is not None
                else _disallowed_agent_ids(data)
            ),
        ),
        "preflight": preflight_projection_adapter(
            user_directions=directions, runtime_evidence=runtime
        ),
    }
    staged_intent = intent or {
        "risk_classification": "ordinary_scoped_recoverable",
        "authority_ref": issue_url,
        "staged_action_digest": projection_sha256(
            {
                "kind": "validate_plan_projections",
                "implementation_issue_url": issue_url,
                "implementation_body_sha256": issue_body_sha256(body),
            }
        ),
    }
    envelope = run_projection_kernel(
        implementation_body_bytes,
        authority={
            "kind": "github_issue",
            "locator": issue_url,
            "source_revision": issue_body_sha256(body),
        },
        versions={
            "kernel": PROJECTION_KERNEL_VERSION,
            "reader": "github-issue-reader/v1",
            "canonicalizer": "github-issue-body/v1",
        },
        policy_versions={
            "assurance": "assurance-policy/v1",
            "visual": "visual-applicability/v1",
            "graph": "graph-policy/v1",
        },
        assurance=assurance,
        capsule_generation={
            field: capsule.get(field) for field in ("capsule_id", "generation", "digest")
        },
        adapters=adapters,
        required_slots=PLAN_PROJECTION_REQUIRED_SLOTS,
        parent_bundle=parent_bundle,
        prepared_at=prepared_at,
        completed_at=completed_at,
        intent=staged_intent,
        audit_receipts=audit_receipts or [],
        provider_receipts=provider_receipts or [],
        graph_operations=graph_operations or [],
        gate_outcomes=gate_outcomes or [],
        external_actions=external_actions or [],
    )
    return {
        "bundle": envelope["bundle"],
        "receipt": envelope["receipt"],
        "required_slots": list(PLAN_PROJECTION_REQUIRED_SLOTS),
    }


def validate_projection_transaction_evidence(
    data: Any, implementation_body_bytes: bytes | None = None
) -> list[str]:
    """Validate the envelope and replay every required adapter when cut over."""

    if not isinstance(data, dict):
        return ["projection transaction evidence container must be an object"]
    flag = data.get("projection_transaction_evidence_required")
    if "projection_transaction_evidence_required" in data and not isinstance(flag, bool):
        return ["projection_transaction_evidence_required must be boolean"]
    required = flag is True
    evidence = data.get("projection_transaction_evidence")
    if evidence in (None, {}):
        return ["projection_transaction_evidence is required"] if required else []
    if not isinstance(evidence, dict):
        return ["projection_transaction_evidence must be an object"]
    unknown = sorted(set(evidence) - {"bundle", "receipt", "required_slots"})
    errors = (
        ["projection_transaction_evidence has unknown fields: " + ", ".join(unknown)]
        if unknown
        else []
    )
    required_slots = evidence.get("required_slots")
    if required_slots is not None and not isinstance(required_slots, list):
        errors.append("projection_transaction_evidence.required_slots must be an array")
        required_slots = None
    if required and required_slots != list(PLAN_PROJECTION_REQUIRED_SLOTS):
        errors.append(
            "projection_transaction_evidence.required_slots must exactly list "
            + ", ".join(PLAN_PROJECTION_REQUIRED_SLOTS)
        )
    bundle, receipt = evidence.get("bundle"), evidence.get("receipt")
    if bundle is None:
        errors.append("projection_transaction_evidence.bundle is required")
    else:
        errors.extend(
            f"projection_transaction_evidence.bundle: {error}"
            for error in validate_projection_bundle(
                bundle,
                authority_bytes=implementation_body_bytes if required else None,
                required_slots=required_slots,
            )
        )
    if receipt is None:
        errors.append("projection_transaction_evidence.receipt is required")
    else:
        errors.extend(
            f"projection_transaction_evidence.receipt: {error}"
            for error in validate_projection_transaction_receipt(
                receipt,
                bundle=bundle if isinstance(bundle, dict) else None,
                required_slots=required_slots,
            )
        )
    if not required:
        return errors
    if implementation_body_bytes is None:
        return errors + [
            "projection_transaction_evidence requires current implementation body bytes"
        ]
    if errors or not isinstance(bundle, dict) or not isinstance(receipt, dict):
        return errors
    consumers = {
        "tasks": lambda: tasks_from_projection_bundle(bundle),
        "graph_policy": lambda: graph_policy_from_projection_bundle(bundle),
        "graph_draft": lambda: graph_draft_from_projection_bundle(bundle),
        "visual_disposition": lambda: visual_disposition_from_projection_bundle(bundle),
        "plan_audit_inputs": lambda: validate_plan_audits_from_projection_bundle(bundle),
        "preflight": lambda: preflight_from_projection_bundle(bundle),
    }
    consumed: dict[str, Any] = {}
    for name, consumer in consumers.items():
        try:
            consumed[name] = consumer()
        except (PlanProtocolError, ValueError, TypeError, KeyError) as exc:
            errors.append(f"projection {name} consumer rejected bundle: {exc}")
    if errors:
        return errors
    audit_errors = consumed["plan_audit_inputs"]
    errors.extend(f"projection plan_audit_inputs: {error}" for error in audit_errors)
    preflight = consumed["preflight"]
    if not isinstance(preflight, dict) or preflight.get("status") != "VALID":
        details = preflight.get("errors") if isinstance(preflight, dict) else None
        errors.append(f"projection preflight is not VALID: {details}")
    try:
        expected = assemble_plan_projection_transaction_evidence(
            data,
            implementation_body_bytes,
            prepared_at=str(bundle.get("prepared_at")),
            completed_at=str(receipt.get("completed_at")),
            intent=copy.deepcopy(receipt.get("intent")),
            disallowed_agent_ids=copy.deepcopy(
                bundle["slots"]["plan_audit_inputs"]["payload"]["disallowed_agent_ids"]
            ),
            parent_bundle=copy.deepcopy(bundle.get("parent_bundle")),
        )
    except (PlanProtocolError, ValueError, TypeError, KeyError) as exc:
        errors.append(f"projection replay failed: {exc}")
        return errors
    expected_bundle = expected["bundle"]
    for name in PLAN_PROJECTION_REQUIRED_SLOTS:
        if bundle["slots"].get(name) != expected_bundle["slots"].get(name):
            errors.append(f"projection {name} differs from deterministic replay")
    for field in (
        "authority",
        "versions",
        "policy_versions",
        "assurance",
        "capsule_generation",
        "parent_bundle",
    ):
        if bundle.get(field) != expected_bundle.get(field):
            errors.append(f"projection bundle {field} differs from deterministic replay")
    return errors
