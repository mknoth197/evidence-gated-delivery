#!/usr/bin/env python3
"""Handoff, transition, and retrospective workflow gates."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


GATE_OUTCOME_UNKNOWN = "INSUFFICIENT_EVIDENCE"
GATE_STATUSES = {"active", "retirement_proposed", "retired"}
FINDING_STATUSES = {"finding", "no_finding"}


def validate_gate_economics(
    entries: Any, *, required_gate_ids: tuple[str, ...] = ()
) -> dict[str, list[str]]:
    """Validate local-only gate cost and distinct-contribution records."""

    errors: list[str] = []
    diagnostics: list[str] = []
    if not isinstance(entries, list):
        return {"errors": ["gate_economics must be an array"], "diagnostics": []}
    by_id: dict[str, dict[str, Any]] = {}
    by_failure_class: dict[str, list[str]] = {}
    forbidden_keys = {"telemetry", "remote_endpoint", "ingestion", "network_write"}

    def every_key(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(every_key(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(every_key(item) for item in value))
        return set()

    for index, entry in enumerate(entries):
        label = f"gate_economics[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        leaked = sorted(forbidden_keys & every_key(entry))
        if leaked:
            errors.append(f"{label} contains prohibited remote-observability fields: {', '.join(leaked)}")
        gate_id = entry.get("gate_id")
        if not isinstance(gate_id, str) or not gate_id.strip():
            errors.append(f"{label}.gate_id is required")
            continue
        if gate_id in by_id:
            errors.append(f"duplicate gate_id {gate_id}")
        by_id[gate_id] = entry
        if entry.get("schema_version") != "gate-economics/v1":
            errors.append(f"{label}.schema_version must equal gate-economics/v1")
        for field in ("name", "applicability_predicate", "failure_class"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                errors.append(f"{label}.{field} is required")
        if not isinstance(entry.get("applicable"), bool):
            errors.append(f"{label}.applicable must be boolean")
        for field in ("expected_latency_ms", "actual_latency_ms"):
            value = entry.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                errors.append(f"{label}.{field} must be a nonnegative number")
        if not isinstance(entry.get("cost_proxy"), str) or not entry["cost_proxy"].strip():
            errors.append(f"{label}.cost_proxy must be a string, using UNKNOWN when unavailable")
        finding = entry.get("finding")
        if not isinstance(finding, dict) or finding.get("status") not in FINDING_STATUSES:
            errors.append(f"{label}.finding.status must be finding or no_finding")
        elif not isinstance(finding.get("finding_ids"), list):
            errors.append(f"{label}.finding.finding_ids must be an array")
        remediation = entry.get("remediation")
        if remediation is not None and not isinstance(remediation, str):
            errors.append(f"{label}.remediation must be a string or null")
        denominator = entry.get("raw_denominator")
        duplicate_count = entry.get("duplicate_finding_count")
        duplicate_rate = entry.get("duplicate_finding_rate")
        if isinstance(denominator, bool) or not isinstance(denominator, int) or denominator < 0:
            errors.append(f"{label}.raw_denominator must be a nonnegative integer")
        if isinstance(duplicate_count, bool) or not isinstance(duplicate_count, int) or duplicate_count < 0:
            errors.append(f"{label}.duplicate_finding_count must be a nonnegative integer")
        if isinstance(denominator, int) and isinstance(duplicate_count, int):
            if duplicate_count > denominator:
                errors.append(f"{label}.duplicate_finding_count exceeds raw_denominator")
            expected_rate = 0.0 if denominator == 0 else duplicate_count / denominator
            if isinstance(duplicate_rate, bool) or not isinstance(duplicate_rate, (int, float)) or abs(float(duplicate_rate) - expected_rate) > 1e-9:
                errors.append(f"{label}.duplicate_finding_rate does not match raw counts")
        outcome = entry.get("downstream_outcome")
        if outcome != GATE_OUTCOME_UNKNOWN and not isinstance(outcome, dict):
            errors.append(f"{label}.downstream_outcome must be an object or INSUFFICIENT_EVIDENCE")
        status = entry.get("status")
        if status not in GATE_STATUSES:
            errors.append(f"{label}.status is invalid")
        if status == "retired":
            review = entry.get("human_review")
            if not isinstance(review, dict) or not all(
                isinstance(review.get(field), str) and review[field].strip()
                for field in ("reviewer", "decision", "reviewed_at")
            ):
                errors.append(f"{label} cannot retire without human_review evidence")
        failure_class = entry.get("failure_class")
        if isinstance(failure_class, str) and failure_class.strip():
            by_failure_class.setdefault(failure_class, []).append(gate_id)
    for failure_class, gate_ids in sorted(by_failure_class.items()):
        if len(gate_ids) > 1:
            diagnostics.append(f"overlapping failure_class {failure_class}: {', '.join(sorted(gate_ids))}")
    for gate_id in required_gate_ids:
        entry = by_id.get(gate_id)
        if entry is None:
            errors.append(f"required gate {gate_id} is missing")
        elif entry.get("status") != "active":
            errors.append(f"required gate {gate_id} must remain active")
    return {"errors": errors, "diagnostics": diagnostics}


@dataclass(frozen=True)
class WorkflowGateDependencies:
    nonempty: Callable[..., bool]
    timestamp: Callable[..., Any]
    finite_number: Callable[..., bool]
    collaboration_delegated_audit_evidence: Callable[..., Any]
    agent_session_evidence: Callable[..., Any]
    persisted_delegation_role_matches: Callable[..., bool]
    RETROSPECTIVE_RUBRIC: Mapping[str, int]


def add_handoff_error(
    data: dict[str, Any],
    phase: str,
    errors: list[str],
    *,
    deps: WorkflowGateDependencies,
) -> None:
    if phase == "orchestrate-preapproval":
        return
    continuing_to = data.get("continuing_to")
    expected = {"research": "plan", "plan": "implement-orientation", "implement": "review"}
    if deps.nonempty(continuing_to):
        if phase in expected and continuing_to != expected[phase]:
            errors.append(f"continuing_to for {phase} must be {expected[phase]}")
        return
    invocation = data.get("next_invocation")
    if not deps.nonempty(invocation):
        errors.append("next_invocation is required when the run is not continuing")
        return
    expected_command = {
        "research": "$evidence-gated-delivery plan",
        "plan": "$evidence-gated-delivery implement",
        "implement": "$evidence-gated-delivery review",
    }.get(phase)
    if expected_command and expected_command not in invocation:
        errors.append(f"next_invocation must contain {expected_command}")


def transition_judge_excluded_ids(
    data: dict[str, Any],
    current_judgment: dict[str, Any],
    *,
    prior_role_data: dict[str, Any] | None = None,
    deps: WorkflowGateDependencies,
) -> set[str]:
    def every_declared_id(entries: Any) -> set[str]:
        return {
            entry["agent_id"].strip()
            for entry in entries or []
            if isinstance(entry, dict) and deps.nonempty(entry.get("agent_id"))
        }

    role_sources = (data, prior_role_data) if prior_role_data is not None else (data,)
    excluded: set[str] = set()
    for source in role_sources:
        for field in (
            "contestants", "judges", "implementation_workers", "trace_audits",
            "plan_audits", "phase_retrospectives",
        ):
            excluded |= every_declared_id(source.get(field))
        for label in ("test_reviewer", "acceptance_reviewer"):
            entry = source.get(label)
            if isinstance(entry, dict) and deps.nonempty(entry.get("agent_id")):
                excluded.add(entry["agent_id"].strip())
        excluded |= {
            entry["agent_id"].strip()
            for entry in source.get("phase_transition_judgments", [])
            if isinstance(entry, dict)
            and entry is not current_judgment
            and deps.nonempty(entry.get("agent_id"))
        }
    return excluded


def validate_transition_gate(
    data: dict[str, Any],
    target_phase: str,
    errors: list[str],
    *,
    predecessor_binding: dict[str, Any] | None = None,
    prior_role_data: dict[str, Any] | None = None,
    deps: WorkflowGateDependencies,
) -> None:
    """Authorize a successor only after an independent, evidence-bound technical judgment."""
    predecessor_by_target = {"plan": "research", "implement": "plan", "review": "implement"}
    predecessor = predecessor_by_target.get(target_phase)
    if predecessor is None:
        return
    policy = data.get("automation_policy")
    if not isinstance(policy, dict):
        errors.append("automation_policy is required for an autonomous phase transition")
        return
    if policy.get("default_mode") != "autonomous":
        errors.append("automation_policy.default_mode must be autonomous")
    if policy.get("auto_transition_min_confidence") != 8:
        errors.append("automation_policy.auto_transition_min_confidence must equal 8")
    stops = policy.get("stop_before_phases")
    releases = policy.get("released_stop_gates")
    if not isinstance(stops, list) or any(v not in predecessor_by_target for v in stops):
        errors.append("automation_policy.stop_before_phases must contain only plan, implement, or review")
        stops = []
    if not isinstance(releases, list):
        errors.append("automation_policy.released_stop_gates must be an array")
        releases = []
    if target_phase in stops:
        released = any(
            isinstance(entry, dict)
            and entry.get("phase") == target_phase
            and deps.timestamp(entry.get("released_at")) is not None
            and deps.nonempty(entry.get("user_evidence"))
            for entry in releases
        )
        if not released:
            errors.append(f"human stop gate before {target_phase} is open")

    judgments = data.get("phase_transition_judgments")
    if not isinstance(judgments, list):
        errors.append("phase_transition_judgments must be an array")
        return
    judgment = next(
        (entry for entry in judgments if isinstance(entry, dict) and entry.get("phase") == predecessor),
        None,
    )
    if judgment is None:
        errors.append(f"{predecessor} requires an independent phase transition judgment")
        return
    if judgment.get("successor_phase") != target_phase:
        errors.append(f"{predecessor} transition judgment must name {target_phase} as successor_phase")
    if judgment.get("status") != "pass" or judgment.get("recommendation") != "proceed":
        errors.append(f"{predecessor} transition judgment must pass and recommend proceed")
    confidence = judgment.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, int) or not 8 <= confidence <= 10:
        errors.append(f"{predecessor} transition judgment confidence must be an integer from 8 through 10")
    if not deps.finite_number(judgment.get("technical_accuracy_score"), 3, 4):
        errors.append(f"{predecessor} transition judgment technical_accuracy_score must be 3..4")
    if not isinstance(judgment.get("evidence_ids"), list) or not any(deps.nonempty(item) for item in judgment["evidence_ids"]):
        errors.append(f"{predecessor} transition judgment needs evidence_ids")
    if deps.timestamp(judgment.get("completed_at")) is None or not deps.nonempty(judgment.get("result_sha256")):
        errors.append(f"{predecessor} transition judgment needs completed_at and result_sha256")
    findings = judgment.get("blocking_findings", [])
    if not isinstance(findings, list):
        errors.append(f"{predecessor} transition judgment blocking_findings must be an array")
    elif any(isinstance(finding, dict) and finding.get("severity") in {"high", "critical"} for finding in findings):
        errors.append(f"{predecessor} transition judgment has unresolved high or critical findings")
    binding = predecessor_binding
    if binding is None:
        binding = data.get("phase_receipt_bindings", {}).get(predecessor) if isinstance(data.get("phase_receipt_bindings"), dict) else None
    if not isinstance(binding, dict) or judgment.get("phase_receipt_sha256") != binding.get("receipt_sha256"):
        errors.append(f"{predecessor} transition judgment must bind the predecessor VALID receipt SHA-256")
    else:
        collaboration = judgment.get("receipt_kind") == "collaboration_delegated"
        if collaboration:
            evidence, session_error = deps.collaboration_delegated_audit_evidence(
                data, judgment
            )
        else:
            evidence, session_error = deps.agent_session_evidence(
                judgment.get("agent_id", "")
            )
        if session_error:
            errors.append(f"{predecessor} transition judge session verification failed: {session_error}")
        else:
            assert evidence is not None
            bound_receipt_sha = binding.get("receipt_sha256")
            if not isinstance(bound_receipt_sha, str) or bound_receipt_sha not in evidence["final_message"]:
                errors.append(
                    f"{predecessor} transition judge callback does not name the bound predecessor receipt SHA-256"
                )
            session_meta = evidence["session_meta"]
            subagent = session_meta.get("source", {}).get("subagent", {}).get("thread_spawn", {})
            expected_marker = f"phase transition judge: {predecessor} -> {target_phase}"
            if session_meta.get("thread_source") != "subagent" or subagent.get("depth") != 1:
                errors.append(f"{predecessor} transition judge must be a depth-one Codex subagent")
            if subagent.get("parent_thread_id") != data.get("parent_thread_id"):
                errors.append(f"{predecessor} transition judge does not belong to the current parent thread")
            if collaboration and not deps.persisted_delegation_role_matches(
                evidence.get("delegation_arguments"), expected_marker
            ):
                errors.append(
                    f"{predecessor} transition judge persisted delegation lacks "
                    "the required role marker"
                )
            elif not collaboration and expected_marker not in evidence["prompt"].lower():
                errors.append(f"{predecessor} transition judge prompt lacks the required role marker")
            if judgment.get("result_sha256") != hashlib.sha256(evidence["final_message"].encode()).hexdigest():
                errors.append(f"{predecessor} transition judge result SHA-256 does not match its session")
            if deps.timestamp(judgment.get("completed_at")) != deps.timestamp(evidence.get("completed_at")):
                errors.append(f"{predecessor} transition judge completed_at does not match its session")
    excluded_ids = transition_judge_excluded_ids(
        data, judgment, prior_role_data=prior_role_data, deps=deps
    )
    judgment_agent_id = (
        judgment["agent_id"].strip()
        if deps.nonempty(judgment.get("agent_id"))
        else ""
    )
    if not judgment_agent_id or judgment_agent_id in excluded_ids:
        errors.append(
            f"{predecessor} transition judge must be fresh and independent of "
            "all other workflow roles"
        )
    unresolved_hard_stops = data.get("unresolved_hard_stops", [])
    if not isinstance(unresolved_hard_stops, list):
        errors.append("unresolved_hard_stops must be an array")
    elif any(item in policy.get("hard_stop_categories", []) for item in unresolved_hard_stops):
        errors.append("unresolved hard-stop category blocks autonomous transition")
    decisions = data.get("automation_decisions")
    if not isinstance(decisions, list):
        errors.append("automation_decisions must be an array")
    elif not any(
        isinstance(entry, dict)
        and entry.get("from_phase") == predecessor
        and entry.get("to_phase") == target_phase
        and entry.get("decision") == "auto_proceed"
        and entry.get("judge_receipt_sha256") == judgment.get("result_sha256")
        and deps.timestamp(entry.get("decided_at")) is not None
        for entry in decisions
    ):
        errors.append(f"{predecessor} requires a bound auto_proceed automation decision")


def validate_retrospective_gate(
    data: dict[str, Any],
    phase: str,
    errors: list[str],
    *,
    predecessor_data: dict[str, Any] | None = None,
    predecessor_binding: dict[str, Any] | None = None,
    deps: WorkflowGateDependencies,
) -> None:
    """Require fixed-rubric learning before a later phase is accepted."""
    required_by_phase = {
        "research": (),
        "plan": ("research",),
        "orchestrate-preapproval": ("research", "plan"),
        "implement": ("research", "plan"),
        "review": ("research", "plan", "implement"),
    }
    entries = data.get("phase_retrospectives")
    if not isinstance(entries, list):
        if required_by_phase[phase]:
            errors.append("phase_retrospectives must be an array")
        return
    predecessor_entries = (
        predecessor_data.get("phase_retrospectives", [])
        if isinstance(predecessor_data, dict)
        else []
    )
    by_phase = {
        entry.get("phase"): entry
        for entry in [*predecessor_entries, *entries]
        if isinstance(entry, dict)
    }
    bindings: dict[str, Any] = {}
    bindings_declared = False
    if isinstance(predecessor_data, dict) and isinstance(
        predecessor_data.get("phase_receipt_bindings"), dict
    ):
        bindings_declared = True
        bindings.update(predecessor_data["phase_receipt_bindings"])
    if isinstance(data.get("phase_receipt_bindings"), dict):
        bindings_declared = True
        bindings.update(data["phase_receipt_bindings"])
    imported_phase = None
    if isinstance(predecessor_binding, dict):
        imported_phase = predecessor_binding.get("phase")
        if isinstance(imported_phase, str):
            bindings_declared = True
            bindings[imported_phase] = predecessor_binding
    if required_by_phase[phase] and not bindings_declared:
        errors.append("phase_receipt_bindings must bind every predecessor to its first VALID receipt")
    for predecessor in required_by_phase[phase]:
        binding = bindings.get(predecessor)
        if not isinstance(binding, dict):
            errors.append(f"{predecessor} phase receipt binding is required")
        else:
            receipt_path = Path(str(binding.get("receipt_path", ""))).expanduser()
            validated_at = deps.timestamp(binding.get("validated_at"))
            if predecessor == imported_phase:
                completed_at = validated_at
                explicit_completed_at = deps.timestamp(
                    data.get("phase_timeline", {}).get(f"{predecessor}_completed_at")
                )
                if explicit_completed_at is not None and explicit_completed_at != validated_at:
                    errors.append(
                        f"phase_timeline.{predecessor}_completed_at conflicts with imported predecessor receipt"
                    )
            else:
                timeline_source = predecessor_data if isinstance(predecessor_data, dict) else data
                completed_at = deps.timestamp(
                    timeline_source.get("phase_timeline", {}).get(
                        f"{predecessor}_completed_at"
                    )
                )
            if binding.get("status") != "VALID":
                errors.append(f"{predecessor} phase receipt binding must have VALID status")
            if validated_at is None or completed_at != validated_at:
                errors.append(
                    f"phase_timeline.{predecessor}_completed_at must equal the bound first VALID receipt time"
                )
            if not receipt_path.is_file():
                errors.append(f"{predecessor} bound receipt_path must be an existing file")
            else:
                receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
                if binding.get("receipt_sha256") != receipt_sha:
                    errors.append(f"{predecessor} bound receipt SHA-256 does not match")
                try:
                    receipt = json.loads(receipt_path.read_text())
                except (OSError, json.JSONDecodeError):
                    errors.append(f"{predecessor} bound receipt must be readable JSON")
                else:
                    if (
                        receipt.get("status") != "VALID"
                        or receipt.get("phase") != predecessor
                        or deps.timestamp(receipt.get("validated_at")) != validated_at
                    ):
                        errors.append(
                            f"{predecessor} bound receipt content does not match the manifest binding"
                        )
        entry = by_phase.get(predecessor)
        if not isinstance(entry, dict):
            errors.append(f"phase_retrospectives must include completed {predecessor} retrospective")
            continue
        if entry.get("status") != "completed" or not deps.nonempty(entry.get("agent_id")):
            errors.append(f"{predecessor} retrospective needs completed status and independent agent_id")
        scorecard = entry.get("scorecard")
        if not isinstance(scorecard, dict) or set(scorecard) != deps.RETROSPECTIVE_RUBRIC:
            errors.append(f"{predecessor} retrospective needs the fixed rubric scorecard")
            continue
        if not all(deps.finite_number(value, 0, 4) for value in scorecard.values()):
            errors.append(f"{predecessor} retrospective rubric scores must be 0..4")
        evidence = entry.get("evidence")
        if not isinstance(evidence, dict) or any(
            not isinstance(evidence.get(key), list) or not any(deps.nonempty(item) for item in evidence[key])
            for key in deps.RETROSPECTIVE_RUBRIC
        ):
            errors.append(f"{predecessor} retrospective needs evidence for every rubric dimension")
        if not deps.finite_number(entry.get("total"), 0, 100):
            errors.append(f"{predecessor} retrospective total must be 0..100")
        below_threshold = entry.get("total", 0) < 85 or scorecard.get("evidence_integrity", 0) < 3 or scorecard.get("external_action_verification", 0) < 3
        degraded = entry.get("degradation_detected") is True
        if (below_threshold or degraded) and (
            not isinstance(entry.get("remediation_actions"), list)
            or not any(deps.nonempty(item) for item in entry["remediation_actions"])
            or entry.get("remediation_rechecked") is not True
        ):
            errors.append(f"{predecessor} retrospective remediation must be recorded and rechecked")
