#!/usr/bin/env python3
"""Authenticated Codex collaboration receipt readers."""
from __future__ import annotations

import json
import hashlib
import os
import re
from pathlib import Path
from typing import Any

AGENT_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())

def agent_session_evidence(agent_id: str) -> tuple[dict[str, Any] | None, str | None]:
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    session_files = list((codex_home / "sessions").rglob(f"*{agent_id}.jsonl"))
    if len(session_files) != 1:
        return None, f"expected one local Codex session for {agent_id}, found {len(session_files)}"
    prompt_parts: list[str] = []
    final_message: str | None = None
    session_id: str | None = None
    session_meta: dict[str, Any] | None = None
    completed_at: str | None = None
    completed = False
    try:
        for line in session_files[0].read_text().splitlines():
            item = json.loads(line)
            payload = item.get("payload", {})
            if item.get("type") == "session_meta" and payload.get("id") == agent_id:
                session_id = payload.get("id")
                session_meta = payload
            if item.get("type") == "response_item" and payload.get("type") == "message":
                if payload.get("role") == "user":
                    for content in payload.get("content", []):
                        if isinstance(content, dict) and nonempty(content.get("text")):
                            prompt_parts.append(content["text"])
            if item.get("type") == "event_msg" and payload.get("type") == "task_complete":
                completed = True
                final_message = payload.get("last_agent_message")
                completed_at = item.get("timestamp")
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if session_id != agent_id:
        return None, "session metadata agent ID mismatch"
    if not completed or not nonempty(final_message):
        return None, "auditor session did not complete with a final result"
    return {
        "prompt": "\n".join(prompt_parts),
        "final_message": final_message,
        "session_meta": session_meta or {},
        "completed_at": completed_at,
    }, None


def realtime_delegated_audit_evidence(
    data: dict[str, Any], audit: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """Authenticate a depth-one realtime delegation from the parent session trace.

    Realtime collaboration agents do not create standalone UUID session files. Their start and
    callback events are instead durably recorded in the UUID-backed parent rollout session.
    Accept that representation only for realtime_voice parents and only when both events match
    the exact delegated agent path.
    """
    parent_id = data.get("parent_thread_id")
    agent_path = audit.get("agent_id")
    if not nonempty(parent_id) or not nonempty(agent_path):
        return None, "realtime audit needs parent_thread_id and agent_id"
    if not re.fullmatch(r"/[a-z0-9_]+/[a-z0-9_]+", agent_path):
        return None, "realtime auditor must be a depth-one agent path"
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    session_files = list((codex_home / "sessions").rglob(f"*{parent_id}.jsonl"))
    if len(session_files) != 1:
        return None, f"expected one parent rollout session for {parent_id}, found {len(session_files)}"
    started_at: str | None = None
    final_message: str | None = None
    session_meta: dict[str, Any] | None = None
    try:
        for line in session_files[0].read_text().splitlines():
            item = json.loads(line)
            payload = item.get("payload", {})
            if item.get("type") == "session_meta":
                session_meta = payload
            if item.get("type") == "event_msg" and payload.get("type") == "sub_agent_activity":
                if payload.get("agent_path") == agent_path and payload.get("kind") == "started":
                    started_at = item.get("timestamp")
            if item.get("type") == "response_item" and payload.get("type") == "agent_message":
                if payload.get("author") == agent_path:
                    blocks = payload.get("content", [])
                    text = "\n".join(
                        block.get("text", "") for block in blocks if isinstance(block, dict)
                    )
                    marker = "Payload:\n"
                    if marker in text:
                        final_message = text.split(marker, 1)[1]
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if started_at is None or not nonempty(final_message):
        return None, "parent trace lacks a matching delegated start or completed callback"
    if (session_meta or {}).get("id") != parent_id:
        return None, "parent session metadata ID mismatch"
    if (session_meta or {}).get("thread_source") != "realtime_voice":
        return None, "realtime receipt is only valid for a realtime_voice parent session"
    return {
        "final_message": final_message,
        "session_meta": session_meta or {},
        "delegation_started_at": started_at,
    }, None


def _authenticated_collaboration_edge(
    *,
    codex_home: Path,
    parent_id: str,
    child_id: str,
    child_path: str,
    parent_path: str,
    child_result: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Bind one child session to its parent's persisted spawn, start, and callback."""
    parent_files = list((codex_home / "sessions").rglob(f"*{parent_id}.jsonl"))
    if len(parent_files) != 1:
        return None, (
            f"expected one collaboration parent session for {parent_id}, "
            f"found {len(parent_files)}"
        )

    parent_meta: dict[str, Any] | None = None
    started_at: str | None = None
    spawn_event_id: str | None = None
    callback_message: str | None = None
    try:
        records = [json.loads(line) for line in parent_files[0].read_text().splitlines()]
        for item in records:
            payload = item.get("payload", {})
            if item.get("type") == "session_meta" and payload.get("id") == parent_id:
                parent_meta = payload
            if (
                item.get("type") == "event_msg"
                and payload.get("type") == "sub_agent_activity"
                and payload.get("agent_thread_id") == child_id
                and payload.get("agent_path") == child_path
                and payload.get("kind") == "started"
            ):
                started_at = item.get("timestamp")
                spawn_event_id = payload.get("event_id")
            if (
                item.get("type") == "response_item"
                and payload.get("type") == "agent_message"
                and payload.get("author") == child_path
                and payload.get("recipient") == parent_path
            ):
                text = "\n".join(
                    block.get("text", "")
                    for block in payload.get("content", [])
                    if isinstance(block, dict)
                )
                if "Payload:\n" in text:
                    callback_message = text.split("Payload:\n", 1)[1]
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)

    delegation_arguments: Any = None
    if nonempty(spawn_event_id):
        for item in records:
            payload = item.get("payload", {})
            if (
                item.get("type") == "response_item"
                and payload.get("type") == "function_call"
                and payload.get("namespace") == "collaboration"
                and payload.get("name") == "spawn_agent"
                and payload.get("call_id") == spawn_event_id
            ):
                delegation_arguments = payload.get("arguments")
                break

    if parent_meta is None:
        return None, "collaboration parent session metadata ID mismatch"
    if started_at is None or delegation_arguments is None:
        return None, "parent trace lacks the matching collaboration spawn call and start event"
    if not nonempty(callback_message):
        return None, "parent trace lacks the matching collaboration completed callback"
    if callback_message != child_result:
        return None, "parent callback does not match child task completion"
    return {
        "callback": callback_message,
        "delegation_arguments": delegation_arguments,
        "parent_session_meta": parent_meta,
        "started_at": started_at,
    }, None


def collaboration_delegated_audit_evidence(
    data: dict[str, Any], audit: dict[str, Any], *, session_reader=agent_session_evidence
) -> tuple[dict[str, Any] | None, str | None]:
    """Authenticate Desktop collaboration evidence across a UUID-backed ancestry chain."""
    root_parent_id = data.get("parent_thread_id")
    agent_id = audit.get("agent_id")
    agent_path = audit.get("agent_path")
    if not nonempty(root_parent_id) or not nonempty(agent_id) or not nonempty(agent_path):
        return None, "collaboration audit needs parent_thread_id, agent_id, and agent_path"
    if not AGENT_ID_RE.fullmatch(agent_id.strip()):
        return None, "collaboration auditor must use a UUID agent_id"
    if not re.fullmatch(r"(?:/[a-z0-9_]+){2,}", agent_path.strip()):
        return None, "collaboration auditor must use a canonical descendant agent_path"

    child, child_error = session_reader(agent_id.strip())
    if child_error:
        return None, child_error
    assert child is not None
    child_meta = child["session_meta"]
    spawn_meta = child_meta.get("source", {}).get("subagent", {}).get("thread_spawn", {})
    expected_depth = len([part for part in agent_path.strip().split("/") if part]) - 1
    if (
        child_meta.get("thread_source") != "subagent"
        or spawn_meta.get("depth") != expected_depth
    ):
        return None, "collaboration auditor child depth does not match its agent_path"
    if spawn_meta.get("agent_path") != agent_path:
        return None, "collaboration auditor child agent_path mismatch"
    current_parent_id = spawn_meta.get("parent_thread_id")
    if not nonempty(current_parent_id):
        return None, "collaboration auditor child lacks an immediate parent thread"

    current_id = agent_id.strip()
    current_path = agent_path.strip()
    current_evidence = child
    current_depth = expected_depth
    visited = {agent_id.strip()}
    immediate_started_at: str | None = None
    immediate_parent_meta: dict[str, Any] | None = None
    immediate_arguments: Any = None
    immediate_callback: str | None = None
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))

    while True:
        if current_parent_id in visited or not AGENT_ID_RE.fullmatch(str(current_parent_id)):
            return None, "collaboration auditor ancestry is cyclic or malformed"
        visited.add(str(current_parent_id))
        expected_parent_path = current_path.rsplit("/", 1)[0]
        if not expected_parent_path:
            return None, "collaboration auditor does not descend from the run parent"

        edge, edge_error = _authenticated_collaboration_edge(
            codex_home=codex_home,
            parent_id=str(current_parent_id),
            child_id=current_id,
            child_path=current_path,
            parent_path=expected_parent_path,
            child_result=current_evidence["final_message"],
        )
        if edge_error:
            return None, edge_error
        assert edge is not None

        if immediate_started_at is None:
            immediate_started_at = edge["started_at"]
            immediate_parent_meta = edge["parent_session_meta"]
            immediate_arguments = edge["delegation_arguments"]
            immediate_callback = edge["callback"]

        if current_parent_id == root_parent_id:
            if expected_parent_path != "/root":
                return None, "collaboration auditor root agent_path mismatch"
            break

        ancestor, ancestor_error = session_reader(str(current_parent_id))
        if ancestor_error:
            return None, f"collaboration auditor ancestry verification failed: {ancestor_error}"
        assert ancestor is not None
        ancestor_meta = ancestor["session_meta"]
        ancestor_spawn = (
            ancestor_meta.get("source", {}).get("subagent", {}).get("thread_spawn", {})
        )
        if (
            ancestor_meta.get("thread_source") != "subagent"
            or ancestor_spawn.get("agent_path") != expected_parent_path
            or ancestor_spawn.get("depth") != current_depth - 1
        ):
            return None, "collaboration auditor ancestry path or depth mismatch"
        ancestor_parent_id = ancestor_spawn.get("parent_thread_id")
        if not nonempty(ancestor_parent_id):
            return None, "collaboration auditor does not descend from the run parent"
        current_id = str(current_parent_id)
        current_path = expected_parent_path
        current_evidence = ancestor
        current_depth -= 1
        current_parent_id = ancestor_parent_id

    return {
        "final_message": immediate_callback,
        "session_meta": child_meta,
        "completed_at": child.get("completed_at"),
        "delegation_started_at": immediate_started_at,
        "parent_session_meta": immediate_parent_meta,
        "delegation_arguments": immediate_arguments,
    }, None


def persisted_delegation_role_matches(arguments: Any, expected_marker: str) -> bool:
    """Match a role against plaintext prompt text or the persisted task-name marker.

    Desktop may encrypt the message value in the parent trace. The task_name stays
    visible, so auditor task names are also a required, machine-readable role marker.
    """

    parsed: dict[str, Any] = {}
    if isinstance(arguments, dict):
        parsed = arguments
    elif isinstance(arguments, str):
        try:
            value = json.loads(arguments)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            parsed = value
        elif (
            expected_marker != "Test-Coverage Reviewer"
            and expected_marker.lower() in arguments.lower()
        ):
            return True
    task_name = str(parsed.get("task_name", "")).lower()
    if expected_marker == "Test-Coverage Reviewer":
        return task_name == "test_coverage_reviewer"
    message = parsed.get("message")
    if isinstance(message, str) and expected_marker.lower() in message.lower():
        return True
    tokens = set(re.findall(r"[a-z0-9]+", task_name))
    if expected_marker == "Independent Plan spec auditor":
        return bool(
            re.fullmatch(
                r"independent_plan_spec_auditor(?:_[a-z0-9]+)*", task_name
            )
            and not tokens & {"execution", "phase", "transition"}
        )
    transition = re.fullmatch(
        r"phase transition judge: ([a-z-]+) -> ([a-z-]+)", expected_marker
    )
    if transition:
        expected_task_name = (
            "phase_transition_judge_"
            f"{transition.group(1).replace('-', '_')}_to_"
            f"{transition.group(2).replace('-', '_')}"
        )
        return bool(
            re.fullmatch(
                rf"{re.escape(expected_task_name)}(?:_[1-9][0-9]*)?",
                task_name,
            )
        )
    match = re.fullmatch(r"Execution auditor phase: ([a-z-]+)", expected_marker)
    return bool(
        match
        and re.fullmatch(
            rf"execution_auditor_phase_{match.group(1).replace('-', '_')}(?:_[1-9][0-9]*)?",
            task_name,
        )
    )


def _graph_authorization_candidates(message: str) -> list[str]:
    """Return direct text plus authenticated response-annotation selections."""
    candidates = [message]
    match = re.search(
        r"(?s)<response-annotations>\s*(\[.*?\])\s*</response-annotations>",
        message,
    )
    if match is None:
        return candidates
    try:
        annotations = json.loads(match.group(1))
    except json.JSONDecodeError:
        return candidates
    if not isinstance(annotations, list):
        return candidates
    candidates.extend(
        annotation["text"]
        for annotation in annotations
        if isinstance(annotation, dict) and nonempty(annotation.get("text"))
    )
    return candidates


def _direct_user_request(message: str) -> str:
    """Exclude quoted response annotations when evaluating later revocations."""
    marker = "## My request for Codex:"
    if marker not in message:
        return message
    return message.split(marker, 1)[1].strip()


def _revokes_graph_authorization(message: str, draft_sha: str) -> bool:
    direct = _direct_user_request(message).lower()
    if draft_sha.lower() not in direct:
        return False
    return bool(
        re.search(
            r"\b(?:revoke|rescind|reject|cancel|withdraw)\b"
            r"|\bno longer (?:approve|authorize)\b"
            r"|\bdo not (?:approve|authorize|create|proceed)\b"
            r"|\bchanged? my mind\b",
            direct,
        )
    )


def _authorizes_graph_progress(message: str, draft_sha: str) -> bool:
    """Accept clear user intent, not a required magic sentence or copied hash."""
    direct = _direct_user_request(message).lower()
    if _revokes_graph_authorization(message, draft_sha):
        return False
    draft_token = re.escape(draft_sha.lower())
    exact_draft = re.search(
        rf"(?:approve|authorize)\s+(?:the\s+)?(?:graph\s+draft|draft\s+graph)\s+{draft_token}",
        direct,
    )
    broad_delegation = re.search(
        r"\b(?:you\s+can\s+)?act\s+on\s+my\s+behalf\b"
        r"|\b(?:go\s+ahead|move\s+forward|proceed|continue)\s+(?:with\s+)?"
        r"(?:the\s+)?(?:graph|plan|repair|authorized\s+work)\b",
        direct,
    )
    return bool(exact_draft or broad_delegation)


def verify_parent_graph_authorization(
    data: dict[str, Any],
    authorization: dict[str, Any],
    draft: dict[str, Any],
) -> list[str]:
    """Authenticate a parent authorization that covers this graph transaction."""

    evidence = authorization.get("authorization_evidence")
    if not isinstance(evidence, dict):
        return ["graph authorization evidence must be an authenticated receipt"]
    parent_id = data.get("parent_thread_id")
    draft_sha = draft.get("draft_sha256")
    errors: list[str] = []
    if evidence.get("receipt_kind") != "authenticated_parent_user_message":
        errors.append("graph authorization evidence receipt_kind is invalid")
    if evidence.get("parent_thread_id") != parent_id:
        errors.append("graph authorization evidence parent_thread_id mismatch")
    if evidence.get("draft_sha256") != draft_sha:
        errors.append("graph authorization evidence draft SHA-256 mismatch")
    message_sha = evidence.get("message_sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", str(message_sha)):
        errors.append("graph authorization evidence message_sha256 is invalid")
    if errors or not nonempty(parent_id):
        return errors or ["graph authorization needs parent_thread_id"]

    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parent_files = list((codex_home / "sessions").rglob(f"*{parent_id}.jsonl"))
    if len(parent_files) != 1:
        return [
            f"expected one parent rollout session for {parent_id}, found {len(parent_files)}"
        ]
    matched = False
    later_user_messages: list[str] = []
    try:
        records = [
            json.loads(line)
            for line in parent_files[0].read_text().splitlines()
            if line.strip()
        ]
        for item in records:
            payload = item.get("payload", {})
            if item.get("type") != "response_item" or payload.get("type") != "message":
                continue
            if payload.get("role") != "user":
                continue
            message = "\n".join(
                block.get("text", "")
                for block in payload.get("content", [])
                if isinstance(block, dict)
            )
            if hashlib.sha256(message.encode()).hexdigest() != message_sha:
                if matched:
                    later_user_messages.append(message)
                continue
            if item.get("timestamp") != evidence.get("authorized_at"):
                continue
            affirmative = any(
                _authorizes_graph_progress(candidate, str(draft_sha))
                for candidate in _graph_authorization_candidates(message)
            )
            if not affirmative:
                continue
            matched = True
        if matched and any(
            _revokes_graph_authorization(message, str(draft_sha))
            for message in later_user_messages
        ):
            matched = False
    except (OSError, json.JSONDecodeError) as exc:
        return [f"graph authorization parent trace is unreadable: {exc}"]
    if not matched:
        errors.append(
            "parent trace lacks a user authorization covering the graph transaction"
        )
    return errors


def validate_task_authorization_evidence(
    data: dict[str, Any], authorization: dict[str, Any], task_ids: list[str]
) -> list[str]:
    """Authenticate exact partial-scope task approval from the parent user trace."""

    errors: list[str] = []
    if authorization.get("receipt_kind") != "authenticated_parent_user_message":
        errors.append("partial authorization receipt_kind is invalid")
    parent_id = data.get("parent_thread_id")
    if authorization.get("parent_thread_id") != parent_id or not nonempty(parent_id):
        errors.append("partial authorization parent_thread_id mismatch")
    if authorization.get("authorized_task_ids") != task_ids:
        errors.append("partial authorization task IDs do not match executable scope")
    message_sha = authorization.get("message_sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", str(message_sha)):
        errors.append("partial authorization message_sha256 is invalid")
    quote = authorization.get("quote")
    if not nonempty(quote):
        errors.append("partial authorization quote is required")
    if errors:
        return errors

    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parent_files = list((codex_home / "sessions").rglob(f"*{parent_id}.jsonl"))
    if len(parent_files) != 1:
        return [
            f"expected one parent rollout session for {parent_id}, found {len(parent_files)}"
        ]
    matched = False
    revoked = False
    affirmative = re.compile(
        r"^\s*(?:(?:i\s+)?(?:authorize|approve)(?:\s+partial\s+implementation\s+for)?|"
        r"(?:please\s+)?implement|(?:please\s+)?(?:proceed|continue)(?:\s+with)?)"
        r"\s+(?P<scope>[T0-9,\sand-]+)\s+only[.!]?\s*$",
        re.IGNORECASE,
    )
    negative = re.compile(
        r"\b(?:do not|don't|must not|never|not authorized|revoke|revoked|cancel|stop)\b",
        re.IGNORECASE,
    )
    try:
        for line in parent_files[0].read_text().splitlines():
            item = json.loads(line)
            payload = item.get("payload", {})
            if item.get("type") != "response_item" or payload.get("type") != "message":
                continue
            if payload.get("role") != "user":
                continue
            message = "\n".join(
                block.get("text", "")
                for block in payload.get("content", [])
                if isinstance(block, dict)
            )
            if matched:
                if negative.search(message):
                    revoked = True
                continue
            if hashlib.sha256(message.encode()).hexdigest() != message_sha:
                continue
            if item.get("timestamp") != authorization.get("received_at"):
                continue
            if quote != message or any(task_id not in quote for task_id in task_ids):
                continue
            match = affirmative.fullmatch(message)
            if (
                match is None
                or re.findall(r"T-\d{3}", match.group("scope")) != task_ids
                or negative.search(message)
            ):
                continue
            matched = True
    except (OSError, json.JSONDecodeError) as exc:
        return [f"partial authorization parent trace is unreadable: {exc}"]
    if not matched or revoked:
        errors.append(
            "parent trace lacks a current affirmative user authorization for the executable task IDs"
        )
    return errors
