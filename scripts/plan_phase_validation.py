#!/usr/bin/env python3
"""Pure Plan-protocol phase validation with injected runtime adapters."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from plan_protocol import (
    PLAN_PROTOCOL_V1, PLAN_PROTOCOL_V2, WORKFLOW_VERSION_V2,
    PlanProtocolError, evaluate_graph_policy, issue_body_sha256, parse_tasks,
    plan_audit_callback_marker, privacy_violations, reconcile_graph_state,
    validate_plan_audits, validate_plan_events, validate_protocol_activation_receipt,
    validate_protocol_version, verify_final_graph, verify_graph_authorization,
)


@dataclass(frozen=True)
class PlanProtocolDependencies:
    nonempty: Callable[..., bool]
    timestamp: Callable[..., Any]
    agent_ids: Callable[..., list[str]]
    collaboration_delegated_audit_evidence: Callable[..., Any]
    persisted_delegation_role_matches: Callable[..., bool]
    authoritative_graph_draft_errors: Callable[..., list[str]]
    verify_parent_graph_authorization: Callable[..., list[str]]
    _gh_json: Callable[..., Any]
    _live_graph_capabilities: Callable[..., Any]
    _remote_graph_state: Callable[..., Any]
    _remote_workflow_graph_artifacts: Callable[..., Any]


@dataclass(frozen=True)
class PlanGateDependencies:
    PLAN_HEADINGS: tuple[str, ...]
    add_research_errors: Callable[..., Any]
    validate_plan_identity_and_evidence: Callable[..., None]
    agent_ids: Callable[..., list[str]]
    completed_agent: Callable[..., bool]
    nonempty: Callable[..., bool]
    validate_image_receipts: Callable[..., None]
    finite_number: Callable[..., bool]
    generated_image_file: Callable[..., bool]
    issue_url: Callable[..., bool]
    require_remote_issue: Callable[..., Any]
    validate_plan_protocol_evidence: Callable[..., None]
    validate_disposition: Callable[..., Any]
    reference_present: Callable[..., bool]
    durable_image_url: Callable[..., bool]
    remote_image_sha256: Callable[..., Any]
    markdown_section: Callable[..., str]


def validate_plan_protocol_evidence(
    data: dict[str, Any],
    implementation_body: str,
    errors: list[str],
    *,
    skip_remote: bool,
    deps: PlanProtocolDependencies,
) -> None:
    protocol_errors = validate_protocol_version(data)
    errors.extend(protocol_errors)
    if protocol_errors:
        return
    external_v2_active, activation_errors = validate_protocol_activation_receipt(data)
    errors.extend(activation_errors)
    if (
        data.get("plan_protocol_version") == PLAN_PROTOCOL_V2
        or data.get("workflow_version") == WORKFLOW_VERSION_V2
    ) and not external_v2_active:
        errors.append("plan-protocol/v2 requires a durable external activation receipt")
    events = data.get("plan_events")
    if data.get("plan_protocol_version") == PLAN_PROTOCOL_V1:
        event_errors = validate_plan_events(events) if events is not None else []
        errors.extend(event_errors)
        workflow_proves_v2 = (
            data.get("workflow_version")
            == WORKFLOW_VERSION_V2
        )
        if not event_errors and isinstance(events, list):
            proves_v2 = any(
                isinstance(event, dict)
                and (
                    (
                        event.get("type") == "protocol_initialized"
                        and event.get("payload", {}).get("plan_protocol_version")
                        == PLAN_PROTOCOL_V2
                    )
                    or (
                        event.get("type") == "protocol_migrated"
                        and event.get("payload", {}).get("to_version")
                        == PLAN_PROTOCOL_V2
                    )
                )
                for event in events
            )
        else:
            proves_v2 = False
        if external_v2_active or workflow_proves_v2 or proves_v2:
            errors.append(
                "plan_protocol_version cannot downgrade to plan-protocol/v1 "
                "after durable v2 activation, workflow initialization, or migration"
            )
        return
    if data.get("plan_protocol_version") != PLAN_PROTOCOL_V2:
        return
    for field in (
        "plan_audits",
        "graph_draft",
        "graph_authorization",
        "graph_actions",
        "graph_remote_state",
    ):
        for violation in privacy_violations(data.get(field), f"$.{field}"):
            errors.append(f"privacy sentinel: {violation}")
    event_errors = validate_plan_events(events)
    errors.extend(event_errors)
    event_types = {
        event.get("type")
        for event in events
        if isinstance(event, dict)
    } if isinstance(events, list) else set()
    if not event_errors:
        if not event_types & {"protocol_initialized", "protocol_migrated"}:
            errors.append("plan_events must record protocol initialization or migration")
        for event_type in (
            "candidate_linted",
            "audit_completed",
            "issue_read_back",
            "graph_policy_evaluated",
        ):
            if event_type not in event_types:
                errors.append(f"plan_events must include {event_type}")
    try:
        tasks = parse_tasks(implementation_body)
    except PlanProtocolError as exc:
        errors.append(f"plan-protocol/v2 task grammar failed: {exc}")
        return
    remote_body_sha = issue_body_sha256(implementation_body)
    disallowed_ids = set(deps.agent_ids(data.get("contestants")))
    disallowed_ids |= set(deps.agent_ids(data.get("judges")))
    disallowed_ids |= set(deps.agent_ids(data.get("trace_audits")))
    disallowed_ids |= set(deps.agent_ids(data.get("implementation_workers")))
    disallowed_ids |= {
        entry.get("agent_id")
        for key in ("phase_retrospectives", "phase_transition_judgments")
        for entry in data.get(key, [])
        if isinstance(entry, dict) and deps.nonempty(entry.get("agent_id"))
    }
    errors.extend(
        validate_plan_audits(
            data.get("plan_audits"),
            final_body_sha256=remote_body_sha,
            disallowed_agent_ids=disallowed_ids,
        )
    )
    plan_audits = data.get("plan_audits")
    if isinstance(plan_audits, list):
        for index, audit in enumerate(plan_audits):
            prefix = f"plan_audits[{index}]"
            if not isinstance(audit, dict):
                continue
            if audit.get("receipt_kind") != "collaboration_delegated":
                errors.append(f"{prefix} must use collaboration_delegated provenance")
                continue
            if audit.get("status") != "completed":
                errors.append(f"{prefix}.status must be completed")
            evidence, session_error = deps.collaboration_delegated_audit_evidence(data, audit)
            if session_error:
                errors.append(f"{prefix} session verification failed: {session_error}")
                continue
            assert evidence is not None
            callback = evidence["final_message"]
            expected_marker = "Independent Plan spec auditor"
            if not deps.persisted_delegation_role_matches(
                evidence.get("delegation_arguments"), expected_marker
            ):
                errors.append(
                    f"{prefix} persisted delegation prompt lacks the required role marker"
                )
            if audit.get("callback_sha256") != hashlib.sha256(callback.encode()).hexdigest():
                errors.append(f"{prefix}.callback_sha256 does not match authenticated callback")
            if plan_audit_callback_marker(audit) not in callback:
                errors.append(
                    f"{prefix} authenticated callback does not bind exact semantic audit content"
                )
            if deps.timestamp(audit.get("started_at")) != deps.timestamp(
                evidence.get("delegation_started_at")
            ):
                errors.append(f"{prefix}.started_at does not match delegation")
            if deps.timestamp(audit.get("completed_at")) != deps.timestamp(
                evidence.get("completed_at")
            ):
                errors.append(f"{prefix}.completed_at does not match child completion")
            for evidence_id in audit.get("evidence_ids", []):
                if deps.nonempty(evidence_id) and evidence_id not in callback:
                    errors.append(
                        f"{prefix} authenticated callback does not name {evidence_id}"
                    )
    computed_policy = evaluate_graph_policy(tasks, evaluated_at="2000-01-01T00:00:00Z")
    recorded_policy = data.get("graph_policy_receipt")
    for field in (
        "policy_version",
        "disposition",
        "task_count",
        "edge_count",
        "owner_lanes",
        "task_set_sha256",
    ):
        if not isinstance(recorded_policy, dict) or recorded_policy.get(field) != computed_policy[field]:
            errors.append(f"graph_policy_receipt.{field} does not match authoritative tasks")
    if not isinstance(recorded_policy, dict) or deps.timestamp(recorded_policy.get("evaluated_at")) is None:
        errors.append("graph_policy_receipt.evaluated_at must be an ISO-8601 timestamp")
    if computed_policy["disposition"] == "NO_GRAPH":
        forbidden_event_types = {
            "graph_draft_frozen",
            "graph_authorized",
            "graph_action_recorded",
            "graph_reconciled",
        }
        for event_type in sorted(event_types & forbidden_event_types):
            errors.append(f"NO_GRAPH forbids {event_type} plan events")
        for field in (
            "graph_capability_receipt",
            "graph_draft",
            "graph_authorization",
            "graph_actions",
            "graph_remote_state",
        ):
            if data.get(field) not in (None, {}, []):
                errors.append(f"NO_GRAPH requires {field} to be empty")
        if not skip_remote:
            workflow_children, graph_error = deps._remote_workflow_graph_artifacts(
                data.get("implementation_issue_url", "")
            )
            if graph_error:
                errors.append(f"NO_GRAPH remote read-back failed: {graph_error}")
            elif workflow_children:
                errors.append(
                    "NO_GRAPH parent has workflow-owned graph children: "
                    + ", ".join(workflow_children)
                )
        return
    for event_type in (
        "graph_draft_frozen",
        "graph_authorized",
        "graph_action_recorded",
        "graph_reconciled",
    ):
        if event_type not in event_types:
            errors.append(f"GRAPH_REQUIRED plan_events must include {event_type}")

    repository_match = re.fullmatch(
        r"https://github\.com/([^/]+/[^/]+)/issues/\d+",
        data.get("implementation_issue_url", ""),
    )
    repository = repository_match.group(1) if repository_match else ""
    draft = data.get("graph_draft")
    errors.extend(
        deps.authoritative_graph_draft_errors(
            draft,
            data.get("implementation_issue_url", ""),
            repository,
            tasks,
        )
    )
    capability = data.get("graph_capability_receipt")
    authorization = data.get("graph_authorization")
    login = capability.get("github_login", "") if isinstance(capability, dict) else ""
    account_id = (
        str(capability.get("github_account_id", ""))
        if isinstance(capability, dict)
        else ""
    )
    if not skip_remote:
        identity, identity_error = deps._gh_json(["api", "user"])
        if identity_error:
            errors.append(f"GitHub identity read-back failed: {identity_error}")
        else:
            assert identity is not None
            login = str(identity.get("login", ""))
            account_id = str(identity.get("id", ""))
        repository_readback, repository_error = deps._gh_json(
            ["repo", "view", repository, "--json", "nameWithOwner"]
        )
        if repository_error:
            errors.append(f"GitHub repository read-back failed: {repository_error}")
        elif repository_readback.get("nameWithOwner") != repository:
            errors.append("GitHub repository read-back does not match the graph repository")
        live_capabilities, capability_error = deps._live_graph_capabilities()
        if capability_error:
            errors.append(f"GitHub graph capability preflight failed: {capability_error}")
        else:
            assert live_capabilities is not None
            for field, observed in live_capabilities.items():
                if not isinstance(capability, dict) or capability.get(field) != observed:
                    errors.append(
                        f"graph_capability_receipt.{field} does not match live gh capability"
                    )
            if not all(
                live_capabilities[field]
                for field in (
                    "native_parent_supported",
                    "blocking_supported",
                    "readback_supported",
                )
            ):
                errors.append("live gh lacks required native graph capabilities")
    if isinstance(draft, dict) and isinstance(capability, dict):
        errors.extend(
            verify_graph_authorization(
                authorization,
                draft,
                current_login=login,
                current_account_id=account_id,
                current_repository=repository,
                current_parent_issue_url=data.get("implementation_issue_url", ""),
                capability_receipt=capability,
            )
        )
        errors.extend(
            deps.verify_parent_graph_authorization(data, authorization, draft)
        )
        recorded_remote_state = data.get("graph_remote_state")
        if not skip_remote:
            live_state, live_error = deps._remote_graph_state(
                data.get("implementation_issue_url", "")
            )
            if live_error:
                errors.append(f"remote graph read-back failed: {live_error}")
            elif live_state != recorded_remote_state:
                errors.append("graph_remote_state does not match authenticated GitHub read-back")
        if isinstance(recorded_remote_state, dict):
            reconciliation = reconcile_graph_state(draft, recorded_remote_state)
            if reconciliation.get("classification") != "EXACT_MATCH":
                errors.append(f"remote graph is not exact: {reconciliation}")
            errors.extend(
                verify_final_graph(draft, recorded_remote_state, data.get("graph_actions"))
            )


def add_plan_errors(
    data: dict[str, Any],
    errors: list[str],
    skip_remote: bool,
    visual_phase: str = "plan",
    review_paths: list[str] | None = None,
    *,
    deps: PlanGateDependencies,
) -> None:
    research_body = deps.add_research_errors(data, errors, skip_remote)
    disposition = data.get("visual_artifact_disposition")
    visual_mode = (
        disposition.get("evidence_mode")
        if isinstance(disposition, dict)
        and disposition.get("evidence_mode")
        in {"none", "runtime_capture", "generative_mockup"}
        else "generative_mockup"
    )
    deps.validate_plan_identity_and_evidence(data, errors, visual_mode)
    contestants = data.get("contestants")
    judges = data.get("judges")
    contestant_ids = deps.agent_ids(contestants)
    judge_ids = deps.agent_ids(judges)

    if len(contestant_ids) != 3 or len(set(contestant_ids)) != 3:
        errors.append("exactly three unique completed contestant agents are required")
    candidate_ids: list[str] = []
    concepts: list[str] = []
    if isinstance(contestants, list):
        for index, contestant in enumerate(contestants):
            if not deps.completed_agent(contestant):
                errors.append(f"contestants[{index}] needs UUID agent_id, completed status, and result")
                continue
            candidate_id = contestant.get("candidate_id")
            if not deps.nonempty(candidate_id):
                errors.append(f"contestants[{index}].candidate_id is required")
            else:
                candidate_ids.append(candidate_id.strip())
            required_fields = ["concept"]
            if visual_mode == "generative_mockup":
                required_fields.append("visual_brief")
            for field in required_fields:
                if not deps.nonempty(contestant.get(field)):
                    errors.append(f"contestants[{index}].{field} is required")
            if deps.nonempty(contestant.get("concept")):
                concepts.append(" ".join(contestant["concept"].lower().split()))
    if len(candidate_ids) != 3 or len(set(candidate_ids)) != 3:
        errors.append("exactly three unique candidate IDs are required")
    if len(concepts) != 3 or len(set(concepts)) != 3:
        errors.append("contestant concepts must be distinct")
    candidate_set = set(candidate_ids)

    if visual_mode == "generative_mockup":
        deps.validate_image_receipts(
            data.get("contestant_images"), candidate_set, errors, "contestant_images"
        )
    elif data.get("contestant_images") != []:
        errors.append(f"visual mode {visual_mode} requires empty contestant_images")

    if len(judge_ids) != 2 or len(set(judge_ids)) != 2:
        errors.append("exactly two unique completed judge agents are required")
    if set(contestant_ids) & set(judge_ids):
        errors.append("judge agent IDs must not overlap contestant agent IDs")
    if isinstance(judges, list):
        for index, judge in enumerate(judges):
            if not deps.completed_agent(judge):
                errors.append(f"judges[{index}] needs UUID agent_id, completed status, and result")
                continue
            if not deps.nonempty(judge.get("verdict")):
                errors.append(f"judges[{index}].verdict is required")
            scorecard = judge.get("scorecard")
            if not isinstance(scorecard, dict) or set(scorecard) != candidate_set:
                errors.append(f"judges[{index}].scorecard must cover all candidates exactly")
            elif not all(deps.finite_number(score, 0, 100) for score in scorecard.values()):
                errors.append(f"judges[{index}].scorecard values must be 0..100")
            if not deps.finite_number(judge.get("confidence"), 1, 10):
                errors.append(f"judges[{index}].confidence must be 1..10")

    rubric = data.get("judge_rubric")
    if not isinstance(rubric, list) or len({v.strip() for v in rubric if deps.nonempty(v)}) < 5:
        errors.append("judge_rubric must contain at least five distinct dimensions")

    semantic_reviews = data.get("semantic_visual_reviews")
    if visual_mode != "generative_mockup":
        if semantic_reviews != []:
            errors.append(f"visual mode {visual_mode} requires empty semantic_visual_reviews")
    elif not isinstance(semantic_reviews, list) or len(semantic_reviews) != 3:
        errors.append("exactly three semantic_visual_reviews are required")
    else:
        reviewed: set[str] = set()
        for index, review in enumerate(semantic_reviews):
            if not isinstance(review, dict):
                errors.append(f"semantic_visual_reviews[{index}] must be an object")
                continue
            candidate_id = review.get("candidate_id")
            if candidate_id not in candidate_set:
                errors.append(f"semantic_visual_reviews[{index}].candidate_id is invalid")
            else:
                reviewed.add(candidate_id)
            if review.get("passed") is not True:
                errors.append(f"semantic_visual_reviews[{index}].passed must be true")
            evidence = review.get("evidence")
            if not isinstance(evidence, list) or not any(deps.nonempty(v) for v in evidence):
                errors.append(f"semantic_visual_reviews[{index}].evidence is required")
        if reviewed != candidate_set:
            errors.append("semantic_visual_reviews must cover every candidate")

    winner = data.get("selected_winner")
    if winner not in candidate_set:
        errors.append("selected_winner must identify one candidate")
    differentiators = data.get("synthesized_differentiators")
    if not isinstance(differentiators, list) or not any(deps.nonempty(v) for v in differentiators):
        errors.append("synthesized_differentiators must contain the synthesis decision")
    rejected = data.get("rejected_differentiators")
    if not isinstance(rejected, list):
        errors.append("rejected_differentiators must be an array")
    elif any(
        not isinstance(item, dict)
        or not deps.nonempty(item.get("idea"))
        or not deps.nonempty(item.get("rationale"))
        for item in rejected
    ):
        errors.append("every rejected differentiator needs idea and rationale")

    final_iterations = data.get("final_image_iterations")
    if visual_mode == "generative_mockup":
        deps.validate_image_receipts(final_iterations, None, errors, "final_image_iterations")
        if not isinstance(final_iterations, list) or not final_iterations:
            errors.append("at least one final ImageGen iteration is required")
        elif isinstance(final_iterations[-1], dict):
            final_confidence = final_iterations[-1].get("confidence")
            if not deps.finite_number(final_confidence, 7, 10):
                errors.append("final confidence must be at least 7")
            for index, iteration in enumerate(final_iterations[:-1]):
                if not isinstance(iteration, dict) or not deps.finite_number(
                    iteration.get("confidence"), 1, 6.999
                ):
                    errors.append(
                        f"final_image_iterations[{index}] must record sub-7 confidence"
                    )
    elif final_iterations != []:
        errors.append(f"visual mode {visual_mode} requires empty final_image_iterations")
    if visual_mode != "generative_mockup" and data.get("final_image_url") not in {"", None}:
        errors.append(f"visual mode {visual_mode} requires an empty final_image_url")
    if not deps.finite_number(data.get("synthesis_confidence"), 7, 10):
        errors.append("synthesis_confidence must be between 7 and 10")

    contestant_paths = {
        str(Path(item["path"]).expanduser().resolve())
        for item in data.get("contestant_images", [])
        if isinstance(item, dict) and deps.generated_image_file(item.get("path"))
    }
    contestant_calls = {
        item["imagegen_call_id"].strip()
        for item in data.get("contestant_images", [])
        if isinstance(item, dict) and deps.nonempty(item.get("imagegen_call_id"))
    }
    final_paths = {
        str(Path(item["path"]).expanduser().resolve())
        for item in final_iterations or []
        if isinstance(item, dict) and deps.generated_image_file(item.get("path"))
    }
    final_calls = {
        item["imagegen_call_id"].strip()
        for item in final_iterations or []
        if isinstance(item, dict) and deps.nonempty(item.get("imagegen_call_id"))
    }
    if visual_mode == "generative_mockup" and (
        contestant_paths & final_paths or contestant_calls & final_calls
    ):
        errors.append("final ImageGen receipts must be distinct from every contestant receipt")

    if data.get("feature_to_spec_redirected") is not True:
        errors.append("feature_to_spec_redirected must be true")
    for field in ("mockup_accounting_rows", "acceptance_criteria_count", "implementation_task_count"):
        if not isinstance(data.get(field), int) or isinstance(data.get(field), bool) or data[field] <= 0:
            errors.append(f"{field} must be a positive integer")
    for field in ("out_of_scope", "frozen_constraints"):
        values = data.get(field)
        if not isinstance(values, list) or not any(deps.nonempty(v) for v in values):
            errors.append(f"{field} must contain concrete entries")

    implementation_url = data.get("implementation_issue_url")
    if deps.issue_url(data.get("research_issue_url")) and implementation_url == data.get("research_issue_url"):
        errors.append("research and implementation issue URLs must be distinct")
    implementation_body = deps.require_remote_issue(
        implementation_url,
        "implementation_issue_url",
        deps.PLAN_HEADINGS,
        errors,
        skip_remote,
    )
    if not skip_remote and research_body and implementation_body:
        deps.validate_plan_protocol_evidence(
            data, implementation_body, errors, skip_remote=skip_remote
        )
        authoritative_paths = None
        if visual_phase == "review":
            authoritative_paths = review_paths
        runtime_upper_field = {
            "plan": "plan_completed_at",
            "implement-orientation": "plan_completed_at",
            "implement": "implement_completed_at",
            "review": "review_completed_at",
        }.get(visual_phase)
        validated_mode, _inventory, disposition_errors = deps.validate_disposition(
            disposition,
            implementation_body,
            phase=visual_phase,
            authoritative_paths=authoritative_paths,
            require_embedded_inventory=data.get("plan_protocol_version")
            == PLAN_PROTOCOL_V2,
            authoritative_user_directions=data.get("visual_user_directions"),
            authoritative_runtime_evidence=data.get("runtime_visual_evidence"),
            runtime_evidence_not_before=data.get("run_started_at"),
            runtime_evidence_not_after=(
                data.get("phase_timeline", {}).get(runtime_upper_field)
                if runtime_upper_field
                else None
            ),
        )
        errors.extend(disposition_errors)
        if validated_mode is not None and validated_mode != visual_mode:
            errors.append("visual mode changed during Plan validation")
        if visual_mode == "runtime_capture":
            runtime_evidence = data.get("runtime_visual_evidence")
            if not isinstance(runtime_evidence, list) or not any(
                isinstance(item, dict)
                and deps.nonempty(item.get("kind"))
                and deps.nonempty(item.get("evidence"))
                for item in runtime_evidence
            ):
                errors.append("runtime_capture requires current runtime visual evidence")
        approved_hosts = data.get("approved_artifact_hosts")
        if not isinstance(approved_hosts, list) or not all(
            deps.nonempty(host) and re.fullmatch(r"[a-zA-Z0-9.-]+", host)
            for host in approved_hosts
        ):
            errors.append("approved_artifact_hosts must be an array of hostnames")
            approved_hosts = []
        if not deps.reference_present(research_body, implementation_url):
            errors.append("research issue does not link the implementation issue")
        if not deps.reference_present(implementation_body, data["research_issue_url"]):
            errors.append("implementation issue does not link the research issue")
        final_sha = None
        if visual_mode == "generative_mockup":
            final_sha = (
                final_iterations[-1].get("sha256")
                if isinstance(final_iterations, list)
                and final_iterations
                and isinstance(final_iterations[-1], dict)
                else None
            )
            final_image_url = data.get("final_image_url")
            if not deps.durable_image_url(final_image_url, approved_hosts):
                errors.append("final_image_url must use an approved durable HTTPS artifact host")
            elif final_image_url not in implementation_body:
                errors.append("implementation issue does not durably link the final ImageGen mockup")
            else:
                remote_sha, remote_error = deps.remote_image_sha256(
                    final_image_url, data.get("implementation_issue_url")
                )
                if remote_error:
                    errors.append(f"final ImageGen URL fetch failed: {remote_error}")
                elif remote_sha != final_sha:
                    errors.append("hosted final ImageGen bytes do not match the manifest SHA-256")
        acceptance_section = deps.markdown_section(implementation_body, "acceptance criteria")
        tasks_section = deps.markdown_section(implementation_body, "tasks")
        matrix_section = deps.markdown_section(implementation_body, "mockup accounting matrix")
        design_section = deps.markdown_section(implementation_body, "design")
        ears = re.findall(
            r"(?im)^(?:[-*][ \t]+)?(?:WHEN|WHILE|WHERE|IF)\b.+\bSHALL\b.+$",
            acceptance_section,
        )
        tasks = re.findall(r"(?im)^[ \t]*[-*][ \t]+\[[ xX]\][ \t]+.+$", tasks_section)
        matrix_rows = [
            line
            for line in matrix_section.splitlines()
            if line.strip().startswith("|")
            and "---" not in line
            and "visual requirement" not in line.lower()
        ]
        if len(ears) != data.get("acceptance_criteria_count", 0):
            errors.append("published EARS acceptance count does not match the manifest")
        if len(tasks) != data.get("implementation_task_count", 0):
            errors.append("published checklist task count does not match the manifest")
        if len(matrix_rows) != data.get("mockup_accounting_rows", 0):
            errors.append("published mockup-accounting row count does not match the manifest")
        if "```mermaid" not in design_section.lower():
            errors.append("published implementation issue must include a Mermaid design diagram")
        if visual_mode == "generative_mockup" and (
            not deps.nonempty(final_sha) or final_sha not in implementation_body
        ):
            errors.append("implementation issue does not bind the final image SHA-256")
