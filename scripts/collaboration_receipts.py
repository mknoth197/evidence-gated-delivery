#!/usr/bin/env python3
"""Authenticated Codex collaboration receipt readers."""
from __future__ import annotations

import json
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


def collaboration_delegated_audit_evidence(
    data: dict[str, Any], audit: dict[str, Any], *, session_reader=agent_session_evidence
) -> tuple[dict[str, Any] | None, str | None]:
    """Authenticate Desktop collaboration evidence across parent and child traces."""
    parent_id = data.get("parent_thread_id")
    agent_id = audit.get("agent_id")
    agent_path = audit.get("agent_path")
    if not nonempty(parent_id) or not nonempty(agent_id) or not nonempty(agent_path):
        return None, "collaboration audit needs parent_thread_id, agent_id, and agent_path"
    if not AGENT_ID_RE.fullmatch(agent_id.strip()):
        return None, "collaboration auditor must use a UUID agent_id"
    if not re.fullmatch(r"/[a-z0-9_]+/[a-z0-9_]+", agent_path.strip()):
        return None, "collaboration auditor must use a depth-one agent_path"

    child, child_error = session_reader(agent_id.strip())
    if child_error:
        return None, child_error
    assert child is not None
    child_meta = child["session_meta"]
    spawn_meta = child_meta.get("source", {}).get("subagent", {}).get("thread_spawn", {})
    if child_meta.get("thread_source") != "subagent" or spawn_meta.get("depth") != 1:
        return None, "collaboration auditor child must be a depth-one Codex subagent"
    if spawn_meta.get("parent_thread_id") != parent_id:
        return None, "collaboration auditor child does not belong to the current parent thread"
    if spawn_meta.get("agent_path") != agent_path:
        return None, "collaboration auditor child agent_path mismatch"

    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parent_files = list((codex_home / "sessions").rglob(f"*{parent_id}.jsonl"))
    if len(parent_files) != 1:
        return None, f"expected one parent rollout session for {parent_id}, found {len(parent_files)}"

    parent_meta: dict[str, Any] | None = None
    started_at: str | None = None
    spawn_event_id: str | None = None
    delegation_arguments: Any = None
    callback_message: str | None = None
    try:
        records = [json.loads(line) for line in parent_files[0].read_text().splitlines()]
        for item in records:
            payload = item.get("payload", {})
            if item.get("type") == "session_meta" and payload.get("id") == parent_id:
                parent_meta = payload
            if item.get("type") == "event_msg" and payload.get("type") == "sub_agent_activity":
                if (
                    payload.get("agent_thread_id") == agent_id
                    and payload.get("agent_path") == agent_path
                    and payload.get("kind") == "started"
                ):
                    started_at = item.get("timestamp")
                    spawn_event_id = payload.get("event_id")
            if item.get("type") == "response_item" and payload.get("type") == "agent_message":
                if payload.get("author") == agent_path and payload.get("recipient") == "/root":
                    text = "\n".join(
                        block.get("text", "")
                        for block in payload.get("content", [])
                        if isinstance(block, dict)
                    )
                    if "Payload:\n" in text:
                        callback_message = text.split("Payload:\n", 1)[1]
        spawn_call_seen = False
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
                    spawn_call_seen = True
                    delegation_arguments = payload.get("arguments")
                    break
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)

    if parent_meta is None:
        return None, "parent session metadata ID mismatch"
    if started_at is None or not spawn_call_seen:
        return None, "parent trace lacks the matching collaboration spawn call and start event"
    if not nonempty(callback_message):
        return None, "parent trace lacks the matching collaboration completed callback"
    if callback_message != child["final_message"]:
        return None, "parent callback does not match child task completion"
    return {
        "final_message": callback_message,
        "session_meta": child_meta,
        "completed_at": child.get("completed_at"),
        "delegation_started_at": started_at,
        "parent_session_meta": parent_meta,
        "delegation_arguments": delegation_arguments,
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
        return task_name == expected_task_name
    match = re.fullmatch(r"Execution auditor phase: ([a-z-]+)", expected_marker)
    return bool(
        match
        and task_name
        == f"execution_auditor_phase_{match.group(1).replace('-', '_')}"
    )
