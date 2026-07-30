#!/usr/bin/env python3
"""Authenticated execution-trace audit validation."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class TraceDependencies:
    completed_agent: Callable[..., bool]
    agent_ids: Callable[..., list[str]]
    nonempty: Callable[..., bool]
    realtime_delegated_audit_evidence: Callable[..., Any]
    collaboration_delegated_audit_evidence: Callable[..., Any]
    agent_session_evidence: Callable[..., Any]
    persisted_delegation_role_matches: Callable[..., bool]
    timestamp: Callable[..., Any]


def validate_trace_audit(
    data: dict[str, Any],
    phase: str,
    errors: list[str],
    *,
    deps: TraceDependencies,
) -> None:
    required_phase = "plan" if phase == "orchestrate-preapproval" else phase
    audits = data.get("trace_audits")
    matching = [
        audit
        for audit in audits or []
        if isinstance(audit, dict) and audit.get("phase") == required_phase
    ]
    realtime = len(matching) == 1 and matching[0].get("receipt_kind") == "realtime_delegated"
    collaboration = (
        len(matching) == 1
        and matching[0].get("receipt_kind") == "collaboration_delegated"
    )
    valid_audit = realtime or (len(matching) == 1 and deps.completed_agent(matching[0]))
    if not valid_audit:
        errors.append(f"exactly one completed {required_phase} trace audit is required")
        return
    if matching[0].get("verdict") != "PASS":
        errors.append(f"{required_phase} trace audit verdict must be PASS")
    audit_ids = deps.agent_ids(audits)
    other_ids = set(deps.agent_ids(data.get("contestants"))) | set(deps.agent_ids(data.get("judges")))
    other_ids |= set(deps.agent_ids(data.get("implementation_workers")))
    if len(audit_ids) != len(set(audit_ids)) or set(audit_ids) & other_ids:
        errors.append("trace auditors must be fresh and unique")
    events = matching[0].get("verified_event_ids")
    if not isinstance(events, list) or not any(deps.nonempty(event) for event in events):
        errors.append(f"{required_phase} trace audit must list verified event IDs")
        return
    if realtime:
        evidence, session_error = deps.realtime_delegated_audit_evidence(data, matching[0])
    elif collaboration:
        evidence, session_error = deps.collaboration_delegated_audit_evidence(data, matching[0])
    else:
        evidence, session_error = deps.agent_session_evidence(matching[0]["agent_id"])
    if session_error:
        errors.append(f"{required_phase} trace auditor session verification failed: {session_error}")
        return
    assert evidence is not None
    final_message = evidence["final_message"]
    session_meta = evidence["session_meta"]
    if collaboration:
        expected_marker = f"Execution auditor phase: {required_phase}"
        if matching[0].get("role_marker") != expected_marker:
            errors.append(
                f"{required_phase} collaboration auditor receipt lacks the required role marker"
            )
        if not deps.persisted_delegation_role_matches(
            evidence.get("delegation_arguments"), expected_marker
        ):
            errors.append(
                f"{required_phase} collaboration auditor persisted delegation prompt "
                "lacks the required role marker"
            )
    elif not realtime:
        prompt = evidence["prompt"]
        subagent = session_meta.get("source", {}).get("subagent", {}).get("thread_spawn", {})
        if session_meta.get("thread_source") != "subagent" or subagent.get("depth") != 1:
            errors.append(f"{required_phase} auditor must be a depth-one Codex subagent")
        parent_thread_id = data.get("parent_thread_id")
        if not deps.nonempty(parent_thread_id) or subagent.get("parent_thread_id") != parent_thread_id:
            errors.append(f"{required_phase} auditor does not belong to the current parent thread")
        if f"execution auditor phase: {required_phase}" not in prompt.lower():
            errors.append(f"{required_phase} auditor session prompt lacks the required role marker")
    elif matching[0].get("role_marker") != f"Execution auditor phase: {required_phase}":
        errors.append(f"{required_phase} realtime auditor receipt lacks the required role marker")
    session_started = deps.timestamp(
        evidence.get("delegation_started_at")
        if realtime or collaboration
        else session_meta.get("timestamp")
    )
    run_started = deps.timestamp(data.get("run_started_at"))
    if session_started is None or run_started is None or session_started < run_started:
        errors.append(f"{required_phase} auditor session is stale relative to this run")
    if not realtime:
        audit_completed = deps.timestamp(evidence.get("completed_at"))
        phase_completed = deps.timestamp(
            data.get("phase_timeline", {}).get(f"{required_phase}_completed_at")
        )
        if audit_completed is None or phase_completed is None or phase_completed < audit_completed:
            errors.append(
                f"phase_timeline.{required_phase}_completed_at must be at or after the authenticated auditor completion"
            )
    result_sha = matching[0].get("result_sha256")
    actual_sha = hashlib.sha256(final_message.encode()).hexdigest()
    if result_sha != actual_sha:
        errors.append(f"{required_phase} auditor result SHA-256 does not match its session")
    normalized_result = final_message.lstrip()
    if realtime and normalized_result.startswith("[COMPLETE]"):
        normalized_result = normalized_result[len("[COMPLETE]") :].lstrip()
    if not normalized_result.startswith("PASS"):
        errors.append(f"{required_phase} auditor session result must begin with PASS")
    for event in events:
        if deps.nonempty(event) and event not in final_message:
            errors.append(f"{required_phase} auditor result does not name evidence ID {event}")
