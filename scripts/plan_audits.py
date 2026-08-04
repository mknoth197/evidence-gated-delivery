#!/usr/bin/env python3
"""Plan audit receipt and remediation-lineage validation."""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Iterable, Mapping

from plan_tasks import authority_text, present_projection_slot, projection_payload

from plan_protocol_core import (
    AUDIT_KINDS, FINDING_DISPOSITIONS, FINDING_SEVERITIES,
    PlanProtocolError, sha256_json,
)
from plan_events import _validate_iso8601

PLAN_AUDIT_INPUTS_PROJECTION_VERSION = "plan-audit-inputs-projection/v1"
_PLAN_AUDIT_PAYLOAD_FIELDS = frozenset(
    (
        "adapter_version",
        "input_digest",
        "final_body_sha256",
        "audits",
        "disallowed_agent_ids",
    )
)


def plan_audit_inputs_projection_adapter(
    *, audits: Iterable[dict[str, Any]], disallowed_agent_ids: Iterable[str] = ()
):
    """Bind auditor proof inputs to the same immutable bytes as every projection."""

    import copy
    from plan_protocol_core import issue_body_sha256

    frozen_audits = copy.deepcopy(list(audits))
    frozen_disallowed = sorted({str(value) for value in disallowed_agent_ids})

    def project(
        authority_bytes: bytes,
        authority_digest: str,
        versions: Mapping[str, str],
    ) -> dict[str, Any]:
        payload = {
            "adapter_version": PLAN_AUDIT_INPUTS_PROJECTION_VERSION,
            "input_digest": authority_digest,
            "final_body_sha256": issue_body_sha256(
                authority_text(authority_bytes)
            ),
            "audits": copy.deepcopy(frozen_audits),
            "disallowed_agent_ids": list(frozen_disallowed),
        }
        return {
            "authority_digest": authority_digest,
            "versions": dict(versions),
            "slot": present_projection_slot(
                payload, PLAN_AUDIT_INPUTS_PROJECTION_VERSION
            ),
        }

    return project


def validate_plan_audits_from_projection_bundle(
    bundle: Mapping[str, Any], slot_name: str = "plan_audit_inputs"
) -> list[str]:
    payload = projection_payload(
        bundle,
        slot_name,
        projection_version=PLAN_AUDIT_INPUTS_PROJECTION_VERSION,
        payload_fields=_PLAN_AUDIT_PAYLOAD_FIELDS,
    )
    return validate_plan_audits(
        payload["audits"],
        final_body_sha256=payload["final_body_sha256"],
        disallowed_agent_ids=payload["disallowed_agent_ids"],
    )

def _audit_result_hash(audit: dict[str, Any]) -> str:
    return sha256_json({key: value for key, value in audit.items() if key != "result_sha256"})


def plan_audit_callback_payload(audit: dict[str, Any]) -> dict[str, Any]:
    """Return the semantic audit content an authenticated callback must bind."""
    findings = audit.get("findings")
    unresolved = any(
        isinstance(finding, dict)
        and finding.get("disposition") == "open"
        and finding.get("severity") in ("Blocker", "High", "Medium")
        for finding in findings if isinstance(findings, list)
    )
    return {
        "audit_id": audit.get("audit_id"),
        "kind": audit.get("kind"),
        "reviewed_body_sha256": audit.get("reviewed_body_sha256"),
        "evidence_ids": audit.get("evidence_ids"),
        "findings": findings,
        "predecessor_audit_id": audit.get("predecessor_audit_id"),
        "predecessor_finding_ids": audit.get("predecessor_finding_ids"),
        "verdict": "BLOCKED" if unresolved else "PASS",
    }


def plan_audit_callback_marker(audit: dict[str, Any]) -> str:
    """Return the content-addressed marker required in the auditor callback."""
    return (
        "PLAN_AUDIT_RECEIPT_SHA256: "
        f"{sha256_json(plan_audit_callback_payload(audit))}"
    )


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
            if (
                isinstance(finding, dict)
                and finding.get("disposition") == "verified_fixed"
                and kind != "remediation_recheck"
            ):
                errors.append(
                    f"{prefix}.findings[{finding_index}].disposition "
                    "verified_fixed is only valid in remediation_recheck"
                )
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
