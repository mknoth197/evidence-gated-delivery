#!/usr/bin/env python3
"""Validate evidence-gated-delivery phase transition receipts."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from visual_applicability import validate_disposition
from plan_protocol import (
    PLAN_PROTOCOL_V1,
    PLAN_PROTOCOL_V2,
    WORKFLOW_VERSION_V2,
    PlanProtocolError,
    evaluate_graph_policy,
    issue_body_sha256,
    parse_tasks,
    privacy_violations,
    reconcile_graph_state,
    validate_graph_draft,
    validate_plan_audits,
    validate_plan_events,
    validate_protocol_activation_receipt,
    validate_protocol_version,
    verify_final_graph,
    verify_graph_authorization,
)

GITHUB_ISSUE_RE = re.compile(r"^https://github\.com/[^/]+/[^/]+/issues/\d+$")
GITHUB_PR_RE = re.compile(r"^https://github\.com/[^/]+/[^/]+/pull/\d+$")
AGENT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
PHASES = ("research", "plan", "orchestrate-preapproval", "implement", "review")
MODE_BY_PHASE = {
    "research": {"research", "orchestrate"},
    "plan": {"plan", "orchestrate"},
    "orchestrate-preapproval": {"orchestrate"},
    "implement": {"implement", "orchestrate"},
    "review": {"review"},
}
RESEARCH_HEADINGS = ("evidence", "observed facts", "inferences", "unresolved questions")
PLAN_HEADINGS = (
    "problem statement",
    "personas",
    "value assessment",
    "user stories",
    "design",
    "tasks",
    "out of scope",
    "acceptance criteria",
    "mockup accounting matrix",
    "cross-reference",
)
RETROSPECTIVE_RUBRIC = {
    "evidence_integrity",
    "external_action_verification",
    "workstream_identity",
    "phase_contract_compliance",
    "semantic_and_privacy_safety",
    "delivery_reliability",
    "learning_quality",
}


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def finite_number(value: Any, minimum: float | None = None, maximum: float | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    if minimum is not None and value < minimum:
        return False
    return maximum is None or value <= maximum


def timestamp(value: Any) -> datetime | None:
    if not nonempty(value):
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def validate_timeline(data: dict[str, Any], phase: str, errors: list[str]) -> None:
    started = timestamp(data.get("run_started_at"))
    timeline = data.get("phase_timeline")
    if started is None:
        errors.append("run_started_at must be an ISO-8601 timestamp")
        return
    if not isinstance(timeline, dict):
        errors.append("phase_timeline must be an object")
        return
    required = ["research_started_at", "research_completed_at"]
    if phase in {"plan", "orchestrate-preapproval", "implement", "review"}:
        required += ["plan_started_at", "plan_completed_at"]
    values = {field: timestamp(timeline.get(field)) for field in required}
    for field, value in values.items():
        if value is None:
            errors.append(f"phase_timeline.{field} must be an ISO-8601 timestamp")
    if any(value is None for value in values.values()):
        return
    ordered = [started] + [values[field] for field in required]
    if ordered != sorted(ordered):
        errors.append("phase_timeline must prove Research completed before Plan began")


def issue_url(value: Any) -> bool:
    return nonempty(value) and bool(GITHUB_ISSUE_RE.match(value.strip()))


def pr_url(value: Any) -> bool:
    return nonempty(value) and bool(GITHUB_PR_RE.match(value.strip()))


def durable_image_url(value: Any, extra_hosts: list[str] | None = None) -> bool:
    if not nonempty(value):
        return False
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        return False
    if host == "github.com" and parsed.path.startswith("/user-attachments/"):
        return True
    if host == "user-images.githubusercontent.com":
        return True
    default_allowed = (
        host == "openai.site"
        or host.endswith(".openai.site")
        or host == "sites.openai.com"
        or host.endswith(".sites.openai.com")
    )
    return default_allowed or any(
        host == value.lower() or host.endswith(f".{value.lower()}")
        for value in (extra_hosts or [])
    )


def private_github_attachment_url(url: str, issue_url: str) -> tuple[str | None, str | None]:
    issue_match = re.fullmatch(
        r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)", issue_url.rstrip("/")
    )
    asset_id = url.rstrip("/").split("/")[-1]
    if issue_match is None or not asset_id:
        return None, "private GitHub attachment resolution requires an exact issue URL"
    owner, repo, number = issue_match.groups()
    command = [
        "gh",
        "api",
        f"repos/{owner}/{repo}/issues/{number}/comments?per_page=100",
        "-H",
        "Accept: application/vnd.github.html+json",
        "--paginate",
    ]
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        pages = json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        return None, f"authenticated GitHub attachment resolution failed: {exc}"
    comments = pages if isinstance(pages, list) else []
    rendered = "\n".join(
        str(comment.get("body_html", "")) for comment in comments if isinstance(comment, dict)
    )
    candidates = re.findall(r'https://private-user-images\.githubusercontent\.com/[^"\s<>]+', rendered)
    for candidate in candidates:
        resolved = html.unescape(candidate)
        if asset_id in resolved:
            return resolved, None
    return None, "authenticated GitHub issue read-back lacks the referenced attachment"


def remote_image_sha256(
    url: str, github_issue_url: str | None = None
) -> tuple[str | None, str | None]:
    request = Request(url, headers={"User-Agent": "evidence-gated-delivery/1.0"})
    try:
        with urlopen(request, timeout=30) as response:
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/"):
                return None, f"content type is {content_type}, not image/*"
            payload = response.read(20 * 1024 * 1024 + 1)
    except HTTPError as exc:
        if (
            exc.code == 404
            and github_issue_url
            and urlparse(url).hostname == "github.com"
            and urlparse(url).path.startswith("/user-attachments/")
        ):
            resolved, resolution_error = private_github_attachment_url(url, github_issue_url)
            if resolution_error:
                return None, resolution_error
            return remote_image_sha256(resolved or "")
        return None, str(exc)
    except (URLError, TimeoutError) as exc:
        return None, str(exc)
    if len(payload) > 20 * 1024 * 1024:
        return None, "artifact exceeds 20 MiB validation limit"
    return hashlib.sha256(payload).hexdigest(), None


def completed_agent(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and nonempty(entry.get("agent_id"))
        and bool(AGENT_ID_RE.match(entry["agent_id"].strip()))
        and entry.get("status") == "completed"
        and nonempty(entry.get("result"))
    )


def generated_image_file(value: Any) -> bool:
    if not nonempty(value):
        return False
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.stat().st_size == 0:
        return False
    allowed_roots = (
        (Path.home() / ".codex" / "generated_images").resolve(),
        (Path.home() / ".codex" / "visualizations").resolve(),
    )
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        return False
    if not any(path.is_relative_to(root) for root in allowed_roots):
        return False
    header = path.read_bytes()[:12]
    return (
        header.startswith(b"\x89PNG\r\n\x1a\n")
        or header.startswith(b"\xff\xd8\xff")
        or (header.startswith(b"RIFF") and header[8:12] == b"WEBP")
    )


def agent_ids(entries: Any) -> list[str]:
    if not isinstance(entries, list):
        return []
    return [entry["agent_id"].strip() for entry in entries if completed_agent(entry)]


def issue_number(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def reference_present(body: str, url: str) -> bool:
    return url in body


def markdown_section(body: str, heading: str) -> str:
    match = re.search(
        rf"(?ims)^##[ \t]+{re.escape(heading)}[ \t]*$\n(.*?)(?=^##[ \t]+|\Z)",
        body,
    )
    return match.group(1).strip() if match else ""


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
    data: dict[str, Any], audit: dict[str, Any]
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

    child, child_error = agent_session_evidence(agent_id.strip())
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
        and match.group(1).replace("-", "_") in task_name
        and ("audit" in tokens or "auditor" in tokens)
    )


def spec_hashes(root: Path) -> dict[str, str]:
    spec_root = root / ".github" / "specs"
    if not spec_root.exists():
        return {}
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(spec_root.rglob("*"))
        if path.is_file()
    }


def validate_trace_audit(data: dict[str, Any], phase: str, errors: list[str]) -> None:
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
    valid_audit = realtime or (len(matching) == 1 and completed_agent(matching[0]))
    if not valid_audit:
        errors.append(f"exactly one completed {required_phase} trace audit is required")
        return
    if matching[0].get("verdict") != "PASS":
        errors.append(f"{required_phase} trace audit verdict must be PASS")
    audit_ids = agent_ids(audits)
    other_ids = set(agent_ids(data.get("contestants"))) | set(agent_ids(data.get("judges")))
    other_ids |= set(agent_ids(data.get("implementation_workers")))
    if len(audit_ids) != len(set(audit_ids)) or set(audit_ids) & other_ids:
        errors.append("trace auditors must be fresh and unique")
    events = matching[0].get("verified_event_ids")
    if not isinstance(events, list) or not any(nonempty(event) for event in events):
        errors.append(f"{required_phase} trace audit must list verified event IDs")
        return
    if realtime:
        evidence, session_error = realtime_delegated_audit_evidence(data, matching[0])
    elif collaboration:
        evidence, session_error = collaboration_delegated_audit_evidence(data, matching[0])
    else:
        evidence, session_error = agent_session_evidence(matching[0]["agent_id"])
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
        if not persisted_delegation_role_matches(
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
        if not nonempty(parent_thread_id) or subagent.get("parent_thread_id") != parent_thread_id:
            errors.append(f"{required_phase} auditor does not belong to the current parent thread")
        if f"execution auditor phase: {required_phase}" not in prompt.lower():
            errors.append(f"{required_phase} auditor session prompt lacks the required role marker")
    elif matching[0].get("role_marker") != f"Execution auditor phase: {required_phase}":
        errors.append(f"{required_phase} realtime auditor receipt lacks the required role marker")
    session_started = timestamp(
        evidence.get("delegation_started_at")
        if realtime or collaboration
        else session_meta.get("timestamp")
    )
    run_started = timestamp(data.get("run_started_at"))
    if session_started is None or run_started is None or session_started < run_started:
        errors.append(f"{required_phase} auditor session is stale relative to this run")
    if not realtime:
        audit_completed = timestamp(evidence.get("completed_at"))
        phase_completed = timestamp(
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
        if nonempty(event) and event not in final_message:
            errors.append(f"{required_phase} auditor result does not name evidence ID {event}")


def github_readback(url: str, kind: str) -> tuple[str | None, str | None]:
    command = ["gh", kind, "view", url, "--json", "url,body"]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if result.returncode != 0:
        # `gh issue/pr view` uses GraphQL, which can intermittently time out even when the
        # authoritative REST resource is healthy. Preserve remote verification by falling back
        # to the corresponding REST endpoint rather than treating a transport quirk as an
        # artifact failure.
        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 4 and parts[2] in {"issues", "pull"}:
            api_path = f"repos/{parts[0]}/{parts[1]}/issues/{parts[3]}"
            fallback = subprocess.run(
                ["gh", "api", api_path], check=False, capture_output=True, text=True, timeout=30
            )
            if fallback.returncode == 0:
                try:
                    payload = json.loads(fallback.stdout)
                    body = payload.get("body")
                    html_url = payload.get("html_url")
                    if html_url == url and nonempty(body):
                        return body, None
                except json.JSONDecodeError:
                    pass
        return None, result.stderr.strip() or f"gh exited {result.returncode}"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return None, str(exc)
    if payload.get("url") != url:
        return None, f"remote URL mismatch: {payload.get('url')}"
    body = payload.get("body")
    if not nonempty(body):
        return None, "remote body is empty"
    return body, None


def github_pr_oids(url: str) -> tuple[dict[str, str] | None, str | None]:
    """Read the live PR base/head commit identities from GitHub."""

    command = [
        "gh",
        "pr",
        "view",
        url,
        "--json",
        "url,baseRefOid,headRefOid",
    ]
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    payload: dict[str, Any] | None = None
    if result.returncode == 0:
        try:
            candidate = json.loads(result.stdout)
            if isinstance(candidate, dict):
                payload = candidate
        except json.JSONDecodeError as exc:
            return None, str(exc)
    else:
        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 4 or parts[2] != "pull":
            return None, "pull request URL could not be converted to a REST endpoint"
        try:
            fallback = subprocess.run(
                ["gh", "api", f"repos/{parts[0]}/{parts[1]}/pulls/{parts[3]}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, str(exc)
        if fallback.returncode != 0:
            return None, (
                result.stderr.strip()
                or fallback.stderr.strip()
                or f"gh exited {fallback.returncode}"
            )
        try:
            rest = json.loads(fallback.stdout)
            payload = {
                "url": rest.get("html_url"),
                "baseRefOid": rest.get("base", {}).get("sha"),
                "headRefOid": rest.get("head", {}).get("sha"),
            }
        except (AttributeError, json.JSONDecodeError) as exc:
            return None, str(exc)
    assert payload is not None
    if payload.get("url") != url:
        return None, f"remote URL mismatch: {payload.get('url')}"
    base_oid = payload.get("baseRefOid")
    head_oid = payload.get("headRefOid")
    if not all(
        isinstance(oid, str) and re.fullmatch(r"[0-9a-fA-F]{40}", oid)
        for oid in (base_oid, head_oid)
    ):
        return None, "remote PR base/head OIDs must be full git SHAs"
    return {
        "base_oid": base_oid.lower(),
        "head_oid": head_oid.lower(),
    }, None


def require_remote_issue(
    url: Any,
    label: str,
    headings: tuple[str, ...],
    errors: list[str],
    skip_remote: bool,
) -> str | None:
    if not issue_url(url):
        errors.append(f"{label} must be a GitHub issue URL")
        return None
    if skip_remote:
        return None
    body, error = github_readback(url, "issue")
    if error:
        errors.append(f"{label} remote read-back failed: {error}")
        return None
    assert body is not None
    for heading in headings:
        if not re.search(rf"(?im)^##[ \t]+{re.escape(heading)}[ \t]*$", body):
            errors.append(f"{label} body is missing required section: {heading}")
        elif not markdown_section(body, heading):
            errors.append(f"{label} section is empty: {heading}")
    return body


def validate_repo_baseline(data: dict[str, Any], errors: list[str]) -> None:
    repo_root = data.get("repo_root")
    starting_commit = data.get("starting_commit")
    initial_status = data.get("initial_spec_status")
    initial_hashes = data.get("initial_spec_hashes")
    if not nonempty(repo_root):
        errors.append("repo_root is required")
        return
    root = Path(repo_root)
    if not root.is_dir() or not (root / ".git").exists():
        errors.append("repo_root must be an existing git worktree")
        return
    if not nonempty(starting_commit) or not re.fullmatch(r"[0-9a-fA-F]{40}", starting_commit):
        errors.append("starting_commit must be a full git SHA")
        return
    commit = subprocess.run(
        ["git", "cat-file", "-e", f"{starting_commit}^{{commit}}"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0:
        errors.append("starting_commit must identify an existing commit")
        return
    if not isinstance(initial_status, list) or not all(nonempty(line) for line in initial_status):
        if initial_status != []:
            errors.append("initial_spec_status must be an array of status lines")
            return
    if not isinstance(initial_hashes, dict) or not all(
        nonempty(path) and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
        for path, digest in initial_hashes.items()
    ):
        errors.append("initial_spec_hashes must map every baseline spec path to SHA-256")
        return

    status = subprocess.run(
        ["git", "status", "--porcelain", "--", ".github/specs"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    current_status = sorted(line for line in status.stdout.splitlines() if line.strip())
    if current_status != sorted(initial_status):
        errors.append("new or changed repository spec working-tree state detected")
    if spec_hashes(root) != initial_hashes:
        errors.append("repository spec content changed after run start")

    committed = subprocess.run(
        ["git", "diff", "--name-only", f"{starting_commit}..HEAD", "--", ".github/specs"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if committed.stdout.strip():
        errors.append("repository spec commits detected after run start")


def add_research_errors(
    data: dict[str, Any], errors: list[str], skip_remote: bool
) -> str | None:
    research_body = require_remote_issue(
        data.get("research_issue_url"),
        "research_issue_url",
        RESEARCH_HEADINGS,
        errors,
        skip_remote,
    )
    evidence = data.get("research_evidence")
    if not isinstance(evidence, dict):
        errors.append("research_evidence must be an object")
        return research_body
    for source in ("live_product", "source", "live_data", "github"):
        values = evidence.get(source)
        if not isinstance(values, list) or not any(nonempty(value) for value in values):
            errors.append(f"research_evidence.{source} must contain concrete evidence")
    validate_repo_baseline(data, errors)
    return research_body


def validate_image_receipts(
    entries: Any,
    expected_candidate_ids: set[str] | None,
    errors: list[str],
    label: str,
) -> None:
    if not isinstance(entries, list):
        errors.append(f"{label} must be an array")
        return
    paths: list[str] = []
    calls: list[str] = []
    candidates: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"{label}[{index}] must be an object")
            continue
        path = entry.get("path")
        call_id = entry.get("imagegen_call_id")
        if not generated_image_file(path):
            errors.append(
                f"{label}[{index}].path must be a generated PNG/JPEG/WebP under "
                "~/.codex/generated_images or ~/.codex/visualizations"
            )
        else:
            paths.append(str(Path(path).expanduser().resolve()))
            expected_sha = hashlib.sha256(Path(path).expanduser().read_bytes()).hexdigest()
            if entry.get("sha256") != expected_sha:
                errors.append(f"{label}[{index}].sha256 does not match the image")
        if not nonempty(call_id):
            errors.append(f"{label}[{index}].imagegen_call_id is required")
        else:
            calls.append(call_id.strip())
        candidate_id = entry.get("candidate_id")
        if expected_candidate_ids is not None:
            if candidate_id not in expected_candidate_ids:
                errors.append(f"{label}[{index}].candidate_id is invalid")
            else:
                candidates.append(candidate_id)
        confidence = entry.get("confidence")
        if label == "final_image_iterations" and not finite_number(confidence, 1, 10):
            errors.append(f"{label}[{index}].confidence must be between 1 and 10")
    if len(paths) != len(set(paths)):
        errors.append(f"{label} paths must be distinct")
    if len(calls) != len(set(calls)):
        errors.append(f"{label} ImageGen call IDs must be distinct")
    if expected_candidate_ids is not None and set(candidates) != expected_candidate_ids:
        errors.append(f"{label} must cover every candidate exactly once")


def validate_plan_identity_and_evidence(
    data: dict[str, Any],
    errors: list[str],
    visual_mode: str = "generative_mockup",
) -> None:
    identity = data.get("initiative_identity")
    if not isinstance(identity, dict):
        errors.append("initiative_identity is required for Plan and later phases")
    else:
        for field in ("name", "slug", "research_issue_url", "implementation_issue_url"):
            if not nonempty(identity.get(field)):
                errors.append(f"initiative_identity.{field} is required")
        if identity.get("research_issue_url") != data.get("research_issue_url"):
            errors.append("initiative_identity.research_issue_url must match research_issue_url")
        if identity.get("implementation_issue_url") != data.get("implementation_issue_url"):
            errors.append("initiative_identity.implementation_issue_url must match implementation_issue_url")

    grounding = data.get("visual_grounding")
    if visual_mode != "none" and (not isinstance(grounding, list) or not grounding):
        errors.append("visual_grounding must contain a current product-shell observation")
    elif isinstance(grounding, list):
        for index, item in enumerate(grounding):
            if not isinstance(item, dict):
                errors.append(f"visual_grounding[{index}] must be an object")
                continue
            for field in ("surface", "url", "screenshot_path", "sha256"):
                if not nonempty(item.get(field)):
                    errors.append(f"visual_grounding[{index}].{field} is required")
            components = item.get("source_components")
            if not isinstance(components, list) or not any(nonempty(v) for v in components):
                errors.append(f"visual_grounding[{index}].source_components is required")
            if timestamp(item.get("observed_at")) is None:
                errors.append(f"visual_grounding[{index}].observed_at must be an ISO-8601 timestamp")
            path = Path(item.get("screenshot_path", "")).expanduser()
            if path.is_file() and item.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
                errors.append(f"visual_grounding[{index}].sha256 does not match screenshot_path")
            elif not path.is_file():
                errors.append(f"visual_grounding[{index}].screenshot_path must be an existing file")

    required_actions = {
        "plan-issue-readback": "github_issue_readback",
        "research-issue-readback": "github_issue_readback",
    }
    if visual_mode == "generative_mockup":
        required_actions["final-mockup-publication"] = "durable_mockup_publication"
    actions = data.get("external_actions")
    if not isinstance(actions, list):
        errors.append("external_actions must be an array")
        return
    by_id = {action.get("id"): action for action in actions if isinstance(action, dict)}
    for action_id, kind in required_actions.items():
        action = by_id.get(action_id)
        if not isinstance(action, dict):
            errors.append(f"external_actions must include {action_id}")
            continue
        if action.get("kind") != kind or action.get("state") != "verified":
            errors.append(f"external_actions.{action_id} must be a verified {kind}")
        for field in ("attempted_at", "tool_event_id", "result", "readback_evidence"):
            if field == "attempted_at":
                if timestamp(action.get(field)) is None:
                    errors.append(f"external_actions.{action_id}.{field} must be an ISO-8601 timestamp")
            elif not nonempty(action.get(field)):
                errors.append(f"external_actions.{action_id}.{field} is required")


def _gh_json(arguments: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        completed = subprocess.run(
            ["gh", *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return None, str(exc)
    if completed.returncode != 0:
        return None, completed.stderr.strip() or "gh command failed"
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return None, f"gh returned invalid JSON: {exc}"
    if not isinstance(value, dict):
        return None, "gh response must be a JSON object"
    return value, None


def _remote_graph_state(
    implementation_url: str,
) -> tuple[dict[str, Any] | None, str | None]:
    match = re.fullmatch(
        r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)",
        implementation_url,
    )
    if match is None:
        return None, "implementation issue URL cannot identify graph repository and parent"
    owner, repository_name, parent_number = match.groups()
    repository = f"{owner}/{repository_name}"
    parent, error = _gh_json(
        [
            "issue",
            "view",
            parent_number,
            "--repo",
            repository,
            "--json",
            "url,subIssues",
        ]
    )
    if error:
        return None, error
    assert parent is not None
    children: list[dict[str, Any]] = []
    task_by_url: dict[str, str] = {}
    raw_children = parent.get("subIssues")
    if not isinstance(raw_children, list):
        return None, "parent issue read-back lacks subIssues"
    child_payloads: list[dict[str, Any]] = []
    for child_summary in raw_children:
        if not isinstance(child_summary, dict) or not isinstance(
            child_summary.get("number"), int
        ):
            return None, "parent subIssues contains a malformed child"
        child, child_error = _gh_json(
            [
                "issue",
                "view",
                str(child_summary["number"]),
                "--repo",
                repository,
                "--json",
                "url,title,body,parent,blockedBy,blocking",
            ]
        )
        if child_error:
            return None, child_error
        assert child is not None
        marker = re.search(
            r"<!-- evidence-gated-delivery-task:(T-\d{3}) -->",
            str(child.get("body", "")),
        )
        if marker is None:
            return None, f"child {child.get('url')} lacks a stable task marker"
        task_id = marker.group(1)
        if task_id in task_by_url.values():
            return None, f"duplicate stable task marker {task_id}"
        task_by_url[str(child.get("url"))] = task_id
        child_payloads.append(child)
        parent_value = child.get("parent")
        parent_url = (
            parent_value.get("url")
            if isinstance(parent_value, dict)
            else None
        )
        children.append(
            {
                "task_id": task_id,
                "stable_marker": marker.group(0),
                "title": child.get("title"),
                "body_sha256": issue_body_sha256(str(child.get("body", ""))),
                "parent_issue_url": parent_url,
            }
        )
    edges: list[dict[str, str]] = []
    for child in child_payloads:
        blocked_match = re.search(
            r"<!-- evidence-gated-delivery-task:(T-\d{3}) -->",
            str(child.get("body", "")),
        )
        assert blocked_match is not None
        blocked = blocked_match.group(1)
        blocked_by = child.get("blockedBy")
        if not isinstance(blocked_by, list):
            return None, f"child {child.get('url')} lacks blockedBy read-back"
        for blocker in blocked_by:
            blocker_url = blocker.get("url") if isinstance(blocker, dict) else None
            blocker_id = task_by_url.get(str(blocker_url))
            if blocker_id is None:
                return None, f"child {child.get('url')} has an unknown blocker"
            blocker_child = next(
                (
                    payload
                    for payload in child_payloads
                    if task_by_url.get(str(payload.get("url"))) == blocker_id
                ),
                None,
            )
            blocking = blocker_child.get("blocking") if blocker_child else None
            blocked_url = str(child.get("url"))
            blocking_urls = {
                str(value.get("url"))
                for value in blocking or []
                if isinstance(value, dict)
            }
            if not isinstance(blocking, list) or blocked_url not in blocking_urls:
                return None, (
                    f"dependency symmetry mismatch: {blocker_id} does not report "
                    f"{blocked} in blocking"
                )
            edges.append({"blocked": blocked, "blocked_by": blocker_id})
    return {"children": children, "edges": edges}, None


def _live_graph_capabilities() -> tuple[dict[str, Any] | None, str | None]:
    commands = {
        "version": ["gh", "--version"],
        "create": ["gh", "issue", "create", "--help"],
        "edit": ["gh", "issue", "edit", "--help"],
        "view": ["gh", "issue", "view", "--help"],
    }
    outputs: dict[str, str] = {}
    for key, command in commands.items():
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            return None, str(exc)
        if completed.returncode != 0:
            return None, completed.stderr.strip() or f"{' '.join(command)} failed"
        outputs[key] = completed.stdout
    view_help = outputs["view"]
    required_fields = ("parent", "subIssues", "blockedBy", "blocking")
    return {
        "gh_version": outputs["version"].splitlines()[0].strip(),
        "native_parent_supported": "--parent" in outputs["create"],
        "blocking_supported": "--add-blocked-by" in outputs["edit"],
        "readback_supported": all(field in view_help for field in required_fields),
    }, None


def validate_plan_protocol_evidence(
    data: dict[str, Any],
    implementation_body: str,
    errors: list[str],
    *,
    skip_remote: bool,
) -> None:
    protocol_errors = validate_protocol_version(data)
    errors.extend(protocol_errors)
    if protocol_errors:
        return
    external_v2_active, activation_errors = validate_protocol_activation_receipt(data)
    errors.extend(activation_errors)
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
    disallowed_ids = set(agent_ids(data.get("contestants")))
    disallowed_ids |= set(agent_ids(data.get("judges")))
    disallowed_ids |= set(agent_ids(data.get("trace_audits")))
    disallowed_ids |= set(agent_ids(data.get("implementation_workers")))
    disallowed_ids |= {
        entry.get("agent_id")
        for key in ("phase_retrospectives", "phase_transition_judgments")
        for entry in data.get(key, [])
        if isinstance(entry, dict) and nonempty(entry.get("agent_id"))
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
            evidence, session_error = collaboration_delegated_audit_evidence(data, audit)
            if session_error:
                errors.append(f"{prefix} session verification failed: {session_error}")
                continue
            assert evidence is not None
            callback = evidence["final_message"]
            expected_marker = "Independent Plan spec auditor"
            if not persisted_delegation_role_matches(
                evidence.get("delegation_arguments"), expected_marker
            ):
                errors.append(
                    f"{prefix} persisted delegation prompt lacks the required role marker"
                )
            if audit.get("callback_sha256") != hashlib.sha256(callback.encode()).hexdigest():
                errors.append(f"{prefix}.callback_sha256 does not match authenticated callback")
            if timestamp(audit.get("started_at")) != timestamp(
                evidence.get("delegation_started_at")
            ):
                errors.append(f"{prefix}.started_at does not match delegation")
            if timestamp(audit.get("completed_at")) != timestamp(
                evidence.get("completed_at")
            ):
                errors.append(f"{prefix}.completed_at does not match child completion")
            for evidence_id in audit.get("evidence_ids", []):
                if nonempty(evidence_id) and evidence_id not in callback:
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
    if not isinstance(recorded_policy, dict) or timestamp(recorded_policy.get("evaluated_at")) is None:
        errors.append("graph_policy_receipt.evaluated_at must be an ISO-8601 timestamp")
    if computed_policy["disposition"] == "NO_GRAPH":
        return
    for event_type in (
        "graph_draft_frozen",
        "graph_authorized",
        "graph_action_recorded",
        "graph_reconciled",
    ):
        if event_type not in event_types:
            errors.append(f"GRAPH_REQUIRED plan_events must include {event_type}")

    draft = data.get("graph_draft")
    errors.extend(validate_graph_draft(draft))
    capability = data.get("graph_capability_receipt")
    authorization = data.get("graph_authorization")
    repository_match = re.fullmatch(
        r"https://github\.com/([^/]+/[^/]+)/issues/\d+",
        data.get("implementation_issue_url", ""),
    )
    repository = repository_match.group(1) if repository_match else ""
    login = capability.get("github_login", "") if isinstance(capability, dict) else ""
    account_id = (
        str(capability.get("github_account_id", ""))
        if isinstance(capability, dict)
        else ""
    )
    if not skip_remote:
        identity, identity_error = _gh_json(["api", "user"])
        if identity_error:
            errors.append(f"GitHub identity read-back failed: {identity_error}")
        else:
            assert identity is not None
            login = str(identity.get("login", ""))
            account_id = str(identity.get("id", ""))
        repository_readback, repository_error = _gh_json(
            ["repo", "view", repository, "--json", "nameWithOwner"]
        )
        if repository_error:
            errors.append(f"GitHub repository read-back failed: {repository_error}")
        elif repository_readback.get("nameWithOwner") != repository:
            errors.append("GitHub repository read-back does not match the graph repository")
        live_capabilities, capability_error = _live_graph_capabilities()
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
        recorded_remote_state = data.get("graph_remote_state")
        if not skip_remote:
            live_state, live_error = _remote_graph_state(
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
) -> None:
    research_body = add_research_errors(data, errors, skip_remote)
    disposition = data.get("visual_artifact_disposition")
    visual_mode = (
        disposition.get("evidence_mode")
        if isinstance(disposition, dict)
        and disposition.get("evidence_mode")
        in {"none", "runtime_capture", "generative_mockup"}
        else "generative_mockup"
    )
    validate_plan_identity_and_evidence(data, errors, visual_mode)
    contestants = data.get("contestants")
    judges = data.get("judges")
    contestant_ids = agent_ids(contestants)
    judge_ids = agent_ids(judges)

    if len(contestant_ids) != 3 or len(set(contestant_ids)) != 3:
        errors.append("exactly three unique completed contestant agents are required")
    candidate_ids: list[str] = []
    concepts: list[str] = []
    if isinstance(contestants, list):
        for index, contestant in enumerate(contestants):
            if not completed_agent(contestant):
                errors.append(f"contestants[{index}] needs UUID agent_id, completed status, and result")
                continue
            candidate_id = contestant.get("candidate_id")
            if not nonempty(candidate_id):
                errors.append(f"contestants[{index}].candidate_id is required")
            else:
                candidate_ids.append(candidate_id.strip())
            required_fields = ["concept"]
            if visual_mode == "generative_mockup":
                required_fields.append("visual_brief")
            for field in required_fields:
                if not nonempty(contestant.get(field)):
                    errors.append(f"contestants[{index}].{field} is required")
            if nonempty(contestant.get("concept")):
                concepts.append(" ".join(contestant["concept"].lower().split()))
    if len(candidate_ids) != 3 or len(set(candidate_ids)) != 3:
        errors.append("exactly three unique candidate IDs are required")
    if len(concepts) != 3 or len(set(concepts)) != 3:
        errors.append("contestant concepts must be distinct")
    candidate_set = set(candidate_ids)

    if visual_mode == "generative_mockup":
        validate_image_receipts(
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
            if not completed_agent(judge):
                errors.append(f"judges[{index}] needs UUID agent_id, completed status, and result")
                continue
            if not nonempty(judge.get("verdict")):
                errors.append(f"judges[{index}].verdict is required")
            scorecard = judge.get("scorecard")
            if not isinstance(scorecard, dict) or set(scorecard) != candidate_set:
                errors.append(f"judges[{index}].scorecard must cover all candidates exactly")
            elif not all(finite_number(score, 0, 100) for score in scorecard.values()):
                errors.append(f"judges[{index}].scorecard values must be 0..100")
            if not finite_number(judge.get("confidence"), 1, 10):
                errors.append(f"judges[{index}].confidence must be 1..10")

    rubric = data.get("judge_rubric")
    if not isinstance(rubric, list) or len({v.strip() for v in rubric if nonempty(v)}) < 5:
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
            if not isinstance(evidence, list) or not any(nonempty(v) for v in evidence):
                errors.append(f"semantic_visual_reviews[{index}].evidence is required")
        if reviewed != candidate_set:
            errors.append("semantic_visual_reviews must cover every candidate")

    winner = data.get("selected_winner")
    if winner not in candidate_set:
        errors.append("selected_winner must identify one candidate")
    differentiators = data.get("synthesized_differentiators")
    if not isinstance(differentiators, list) or not any(nonempty(v) for v in differentiators):
        errors.append("synthesized_differentiators must contain the synthesis decision")
    rejected = data.get("rejected_differentiators")
    if not isinstance(rejected, list):
        errors.append("rejected_differentiators must be an array")
    elif any(
        not isinstance(item, dict)
        or not nonempty(item.get("idea"))
        or not nonempty(item.get("rationale"))
        for item in rejected
    ):
        errors.append("every rejected differentiator needs idea and rationale")

    final_iterations = data.get("final_image_iterations")
    if visual_mode == "generative_mockup":
        validate_image_receipts(final_iterations, None, errors, "final_image_iterations")
        if not isinstance(final_iterations, list) or not final_iterations:
            errors.append("at least one final ImageGen iteration is required")
        elif isinstance(final_iterations[-1], dict):
            final_confidence = final_iterations[-1].get("confidence")
            if not finite_number(final_confidence, 7, 10):
                errors.append("final confidence must be at least 7")
            for index, iteration in enumerate(final_iterations[:-1]):
                if not isinstance(iteration, dict) or not finite_number(
                    iteration.get("confidence"), 1, 6.999
                ):
                    errors.append(
                        f"final_image_iterations[{index}] must record sub-7 confidence"
                    )
    elif final_iterations != []:
        errors.append(f"visual mode {visual_mode} requires empty final_image_iterations")
    if visual_mode != "generative_mockup" and data.get("final_image_url") not in {"", None}:
        errors.append(f"visual mode {visual_mode} requires an empty final_image_url")
    if not finite_number(data.get("synthesis_confidence"), 7, 10):
        errors.append("synthesis_confidence must be between 7 and 10")

    contestant_paths = {
        str(Path(item["path"]).expanduser().resolve())
        for item in data.get("contestant_images", [])
        if isinstance(item, dict) and generated_image_file(item.get("path"))
    }
    contestant_calls = {
        item["imagegen_call_id"].strip()
        for item in data.get("contestant_images", [])
        if isinstance(item, dict) and nonempty(item.get("imagegen_call_id"))
    }
    final_paths = {
        str(Path(item["path"]).expanduser().resolve())
        for item in final_iterations or []
        if isinstance(item, dict) and generated_image_file(item.get("path"))
    }
    final_calls = {
        item["imagegen_call_id"].strip()
        for item in final_iterations or []
        if isinstance(item, dict) and nonempty(item.get("imagegen_call_id"))
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
        if not isinstance(values, list) or not any(nonempty(v) for v in values):
            errors.append(f"{field} must contain concrete entries")

    implementation_url = data.get("implementation_issue_url")
    if issue_url(data.get("research_issue_url")) and implementation_url == data.get("research_issue_url"):
        errors.append("research and implementation issue URLs must be distinct")
    implementation_body = require_remote_issue(
        implementation_url,
        "implementation_issue_url",
        PLAN_HEADINGS,
        errors,
        skip_remote,
    )
    if not skip_remote and research_body and implementation_body:
        validate_plan_protocol_evidence(
            data, implementation_body, errors, skip_remote=skip_remote
        )
        authoritative_paths = None
        if visual_phase == "review":
            authoritative_paths = review_paths
        validated_mode, _inventory, disposition_errors = validate_disposition(
            disposition,
            implementation_body,
            phase=visual_phase,
            authoritative_paths=authoritative_paths,
            require_embedded_inventory=data.get("plan_protocol_version")
            == PLAN_PROTOCOL_V2,
            authoritative_user_directions=data.get("visual_user_directions"),
        )
        errors.extend(disposition_errors)
        if validated_mode is not None and validated_mode != visual_mode:
            errors.append("visual mode changed during Plan validation")
        if visual_mode == "runtime_capture":
            runtime_evidence = data.get("runtime_visual_evidence")
            if not isinstance(runtime_evidence, list) or not any(
                isinstance(item, dict)
                and nonempty(item.get("kind"))
                and nonempty(item.get("evidence"))
                for item in runtime_evidence
            ):
                errors.append("runtime_capture requires current runtime visual evidence")
        approved_hosts = data.get("approved_artifact_hosts")
        if not isinstance(approved_hosts, list) or not all(
            nonempty(host) and re.fullmatch(r"[a-zA-Z0-9.-]+", host)
            for host in approved_hosts
        ):
            errors.append("approved_artifact_hosts must be an array of hostnames")
            approved_hosts = []
        if not reference_present(research_body, implementation_url):
            errors.append("research issue does not link the implementation issue")
        if not reference_present(implementation_body, data["research_issue_url"]):
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
            if not durable_image_url(final_image_url, approved_hosts):
                errors.append("final_image_url must use an approved durable HTTPS artifact host")
            elif final_image_url not in implementation_body:
                errors.append("implementation issue does not durably link the final ImageGen mockup")
            else:
                remote_sha, remote_error = remote_image_sha256(
                    final_image_url, data.get("implementation_issue_url")
                )
                if remote_error:
                    errors.append(f"final ImageGen URL fetch failed: {remote_error}")
                elif remote_sha != final_sha:
                    errors.append("hosted final ImageGen bytes do not match the manifest SHA-256")
        acceptance_section = markdown_section(implementation_body, "acceptance criteria")
        tasks_section = markdown_section(implementation_body, "tasks")
        matrix_section = markdown_section(implementation_body, "mockup accounting matrix")
        design_section = markdown_section(implementation_body, "design")
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
            not nonempty(final_sha) or final_sha not in implementation_body
        ):
            errors.append("implementation issue does not bind the final image SHA-256")


def add_orientation_errors(data: dict[str, Any], errors: list[str], skip_remote: bool) -> None:
    add_plan_errors(data, errors, skip_remote, visual_phase="implement-orientation")
    if data.get("orientation_complete") is not True:
        errors.append("orientation_complete must be true")
    if data.get("no_mutation_before_approval") is not True:
        errors.append("no_mutation_before_approval must be true")


def review_changed_paths(
    data: dict[str, Any], errors: list[str]
) -> list[str] | None:
    root = Path(data.get("repo_root", ""))
    starting_commit = data.get("starting_commit")
    if not root.is_dir() or not (root / ".git").exists():
        errors.append("Review requires an existing repository worktree for actual-diff binding")
        return None
    if not nonempty(starting_commit) or not re.fullmatch(
        r"[0-9a-fA-F]{40}", starting_commit
    ):
        errors.append("Review requires a full starting_commit for actual-diff binding")
        return None
    pull_url = data.get("pull_request_url")
    if not pr_url(pull_url):
        errors.append("Review requires a GitHub pull request URL for actual-diff binding")
        return None
    remote, remote_error = github_pr_oids(pull_url)
    if remote_error:
        errors.append(f"Review PR commit read-back failed: {remote_error}")
        return None
    assert remote is not None
    base_oid = remote["base_oid"]
    head_oid = remote["head_oid"]
    if starting_commit.lower() != base_oid:
        errors.append("starting_commit does not match the live PR base OID")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if head.returncode != 0 or not re.fullmatch(
        r"[0-9a-fA-F]{40}", head.stdout.strip()
    ):
        errors.append("failed to resolve local HEAD for Review PR binding")
        return None
    if head.stdout.strip().lower() != head_oid:
        errors.append("local HEAD does not match the live PR head OID")
    for oid, label in ((base_oid, "base"), (head_oid, "head")):
        exists = subprocess.run(
            ["git", "cat-file", "-e", f"{oid}^{{commit}}"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if exists.returncode != 0:
            errors.append(f"live PR {label} OID is not present in the local worktree")
            return None
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_oid}..{head_oid}"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        errors.append("failed to derive authoritative changed paths from live PR OIDs")
        return None
    return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())


def validate_reviewer(
    data: dict[str, Any],
    entry: Any,
    label: str,
    errors: list[str],
    *,
    expected_marker: str | None = None,
) -> str | None:
    if not completed_agent(entry):
        errors.append(f"{label} must be a completed agent receipt")
        return None
    if expected_marker is not None:
        if entry.get("receipt_kind") != "collaboration_delegated":
            errors.append(f"{label} must use collaboration_delegated provenance")
            return None
        evidence, session_error = collaboration_delegated_audit_evidence(data, entry)
        if session_error:
            errors.append(f"{label} session verification failed: {session_error}")
            return None
        assert evidence is not None
        callback = evidence["final_message"]
        if not persisted_delegation_role_matches(
            evidence.get("delegation_arguments"), expected_marker
        ):
            errors.append(
                f"{label} persisted delegation lacks the required role marker"
            )
        if entry.get("result") != callback:
            errors.append(f"{label}.result does not match authenticated callback")
        if entry.get("result_sha256") != hashlib.sha256(callback.encode()).hexdigest():
            errors.append(f"{label}.result_sha256 does not match authenticated callback")
        if timestamp(entry.get("started_at")) != timestamp(
            evidence.get("delegation_started_at")
        ):
            errors.append(f"{label}.started_at does not match delegation")
        if timestamp(entry.get("completed_at")) != timestamp(
            evidence.get("completed_at")
        ):
            errors.append(f"{label}.completed_at does not match child completion")
    return entry["agent_id"].strip()


def add_implement_errors(
    data: dict[str, Any],
    errors: list[str],
    skip_remote: bool,
    visual_phase: str = "implement",
    review_paths: list[str] | None = None,
) -> None:
    add_plan_errors(
        data,
        errors,
        skip_remote,
        visual_phase=visual_phase,
        review_paths=review_paths,
    )
    if data.get("orientation_complete") is not True:
        errors.append("orientation_complete must be true")
    mutation_at = timestamp(data.get("first_mutation_at"))
    if mutation_at is None:
        errors.append("first_mutation_at must be an ISO-8601 timestamp")
    if data.get("no_mutation_before_approval") is not True:
        errors.append("no_mutation_before_approval must be true")

    workers = data.get("implementation_workers")
    worker_ids = agent_ids(workers)
    if not worker_ids:
        errors.append("at least one completed implementation worker is required")
    if isinstance(workers, list):
        for index, worker in enumerate(workers):
            if not completed_agent(worker):
                errors.append(f"implementation_workers[{index}] must be a completed agent receipt")
                continue
            ownership = worker.get("ownership")
            if not isinstance(ownership, list) or not any(nonempty(v) for v in ownership):
                errors.append(f"implementation_workers[{index}].ownership is required")
            if not nonempty(worker.get("handoff")):
                errors.append(f"implementation_workers[{index}].handoff is required")

    test_id = validate_reviewer(
        data,
        data.get("test_reviewer"),
        "test_reviewer",
        errors,
        expected_marker="Test-Coverage Reviewer",
    )
    disposition = data.get("visual_artifact_disposition")
    visual_mode = (
        disposition.get("evidence_mode")
        if isinstance(disposition, dict)
        else "generative_mockup"
    )
    acceptance_id = None
    if visual_mode in {"runtime_capture", "generative_mockup"}:
        acceptance_id = validate_reviewer(
            data,
            data.get("acceptance_reviewer"),
            "acceptance_reviewer",
            errors,
        )
    all_prior_ids = set(agent_ids(data.get("contestants"))) | set(agent_ids(data.get("judges")))
    all_ids = worker_ids + [value for value in (test_id, acceptance_id) if value]
    if len(all_ids) != len(set(all_ids)):
        errors.append("implementation workers and reviewers must have unique agent IDs")
    if set(all_ids) & all_prior_ids:
        errors.append("implementation workers/reviewers must be fresh from tournament agents")

    if visual_mode in {"runtime_capture", "generative_mockup"} and data.get(
        "unexplained_mockup_gaps"
    ) != 0:
        errors.append("unexplained_mockup_gaps must equal 0")
    gates = data.get("quality_gates")
    if not isinstance(gates, list) or not gates:
        errors.append("quality_gates must contain structured gate evidence")
    else:
        passed = False
        for index, gate in enumerate(gates):
            if (
                not isinstance(gate, dict)
                or not nonempty(gate.get("name"))
                or gate.get("status") not in {"passed", "skipped"}
                or not nonempty(gate.get("evidence"))
            ):
                errors.append(f"quality_gates[{index}] is invalid")
            elif gate["status"] == "passed":
                passed = True
        if not passed:
            errors.append("at least one quality gate must have passed")

    pull_url = data.get("pull_request_url")
    if not pr_url(pull_url):
        errors.append("pull_request_url must be a GitHub pull request URL")
    elif not skip_remote:
        _, error = github_readback(pull_url, "pr")
        if error:
            errors.append(f"pull_request_url remote read-back failed: {error}")


def add_handoff_error(data: dict[str, Any], phase: str, errors: list[str]) -> None:
    if phase == "orchestrate-preapproval":
        return
    continuing_to = data.get("continuing_to")
    expected = {"research": "plan", "plan": "implement-orientation", "implement": "review"}
    if nonempty(continuing_to):
        if phase in expected and continuing_to != expected[phase]:
            errors.append(f"continuing_to for {phase} must be {expected[phase]}")
        return
    invocation = data.get("next_invocation")
    if not nonempty(invocation):
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
    data: dict[str, Any], current_judgment: dict[str, Any]
) -> set[str]:
    def every_declared_id(entries: Any) -> set[str]:
        return {
            entry["agent_id"].strip()
            for entry in entries or []
            if isinstance(entry, dict) and nonempty(entry.get("agent_id"))
        }

    excluded = every_declared_id(data.get("contestants"))
    excluded |= every_declared_id(data.get("judges"))
    excluded |= every_declared_id(data.get("implementation_workers"))
    excluded |= every_declared_id(data.get("trace_audits"))
    excluded |= every_declared_id(data.get("plan_audits"))
    for label in ("test_reviewer", "acceptance_reviewer"):
        entry = data.get(label)
        if isinstance(entry, dict) and nonempty(entry.get("agent_id")):
            excluded.add(entry["agent_id"].strip())
    excluded |= {
        entry["agent_id"].strip()
        for entry in data.get("phase_retrospectives", [])
        if isinstance(entry, dict) and nonempty(entry.get("agent_id"))
    }
    excluded |= {
        entry["agent_id"].strip()
        for entry in data.get("phase_transition_judgments", [])
        if isinstance(entry, dict)
        and entry is not current_judgment
        and nonempty(entry.get("agent_id"))
    }
    return excluded


def validate_transition_gate(data: dict[str, Any], target_phase: str, errors: list[str]) -> None:
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
            and timestamp(entry.get("released_at")) is not None
            and nonempty(entry.get("user_evidence"))
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
    if not finite_number(judgment.get("technical_accuracy_score"), 3, 4):
        errors.append(f"{predecessor} transition judgment technical_accuracy_score must be 3..4")
    if not isinstance(judgment.get("evidence_ids"), list) or not any(nonempty(item) for item in judgment["evidence_ids"]):
        errors.append(f"{predecessor} transition judgment needs evidence_ids")
    if timestamp(judgment.get("completed_at")) is None or not nonempty(judgment.get("result_sha256")):
        errors.append(f"{predecessor} transition judgment needs completed_at and result_sha256")
    findings = judgment.get("blocking_findings", [])
    if not isinstance(findings, list):
        errors.append(f"{predecessor} transition judgment blocking_findings must be an array")
    elif any(isinstance(finding, dict) and finding.get("severity") in {"high", "critical"} for finding in findings):
        errors.append(f"{predecessor} transition judgment has unresolved high or critical findings")
    binding = data.get("phase_receipt_bindings", {}).get(predecessor) if isinstance(data.get("phase_receipt_bindings"), dict) else None
    if not isinstance(binding, dict) or judgment.get("phase_receipt_sha256") != binding.get("receipt_sha256"):
        errors.append(f"{predecessor} transition judgment must bind the predecessor VALID receipt SHA-256")
    else:
        collaboration = judgment.get("receipt_kind") == "collaboration_delegated"
        if collaboration:
            evidence, session_error = collaboration_delegated_audit_evidence(
                data, judgment
            )
        else:
            evidence, session_error = agent_session_evidence(
                judgment.get("agent_id", "")
            )
        if session_error:
            errors.append(f"{predecessor} transition judge session verification failed: {session_error}")
        else:
            assert evidence is not None
            session_meta = evidence["session_meta"]
            subagent = session_meta.get("source", {}).get("subagent", {}).get("thread_spawn", {})
            expected_marker = f"phase transition judge: {predecessor} -> {target_phase}"
            if session_meta.get("thread_source") != "subagent" or subagent.get("depth") != 1:
                errors.append(f"{predecessor} transition judge must be a depth-one Codex subagent")
            if subagent.get("parent_thread_id") != data.get("parent_thread_id"):
                errors.append(f"{predecessor} transition judge does not belong to the current parent thread")
            if collaboration and not persisted_delegation_role_matches(
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
            if timestamp(judgment.get("completed_at")) != timestamp(evidence.get("completed_at")):
                errors.append(f"{predecessor} transition judge completed_at does not match its session")
    excluded_ids = transition_judge_excluded_ids(data, judgment)
    judgment_agent_id = (
        judgment["agent_id"].strip()
        if nonempty(judgment.get("agent_id"))
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
        and timestamp(entry.get("decided_at")) is not None
        for entry in decisions
    ):
        errors.append(f"{predecessor} requires a bound auto_proceed automation decision")


def validate_retrospective_gate(data: dict[str, Any], phase: str, errors: list[str]) -> None:
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
    by_phase = {entry.get("phase"): entry for entry in entries if isinstance(entry, dict)}
    bindings = data.get("phase_receipt_bindings")
    if required_by_phase[phase] and not isinstance(bindings, dict):
        errors.append("phase_receipt_bindings must bind every predecessor to its first VALID receipt")
        bindings = {}
    for predecessor in required_by_phase[phase]:
        binding = bindings.get(predecessor)
        if not isinstance(binding, dict):
            errors.append(f"{predecessor} phase receipt binding is required")
        else:
            receipt_path = Path(str(binding.get("receipt_path", ""))).expanduser()
            validated_at = timestamp(binding.get("validated_at"))
            completed_at = timestamp(
                data.get("phase_timeline", {}).get(f"{predecessor}_completed_at")
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
                        or timestamp(receipt.get("validated_at")) != validated_at
                    ):
                        errors.append(
                            f"{predecessor} bound receipt content does not match the manifest binding"
                        )
        entry = by_phase.get(predecessor)
        if not isinstance(entry, dict):
            errors.append(f"phase_retrospectives must include completed {predecessor} retrospective")
            continue
        if entry.get("status") != "completed" or not nonempty(entry.get("agent_id")):
            errors.append(f"{predecessor} retrospective needs completed status and independent agent_id")
        scorecard = entry.get("scorecard")
        if not isinstance(scorecard, dict) or set(scorecard) != RETROSPECTIVE_RUBRIC:
            errors.append(f"{predecessor} retrospective needs the fixed rubric scorecard")
            continue
        if not all(finite_number(value, 0, 4) for value in scorecard.values()):
            errors.append(f"{predecessor} retrospective rubric scores must be 0..4")
        evidence = entry.get("evidence")
        if not isinstance(evidence, dict) or any(
            not isinstance(evidence.get(key), list) or not any(nonempty(item) for item in evidence[key])
            for key in RETROSPECTIVE_RUBRIC
        ):
            errors.append(f"{predecessor} retrospective needs evidence for every rubric dimension")
        if not finite_number(entry.get("total"), 0, 100):
            errors.append(f"{predecessor} retrospective total must be 0..100")
        below_threshold = entry.get("total", 0) < 85 or scorecard.get("evidence_integrity", 0) < 3 or scorecard.get("external_action_verification", 0) < 3
        degraded = entry.get("degradation_detected") is True
        if (below_threshold or degraded) and (
            not isinstance(entry.get("remediation_actions"), list)
            or not any(nonempty(item) for item in entry["remediation_actions"])
            or entry.get("remediation_rechecked") is not True
        ):
            errors.append(f"{predecessor} retrospective remediation must be recorded and rechecked")


def validate(data: dict[str, Any], phase: str, skip_remote: bool) -> list[str]:
    errors: list[str] = []
    for field in (
        "run_id",
        "parent_thread_id",
        "mode",
        "goal",
        "selected_mode_reason",
        "workflow_version",
    ):
        if not nonempty(data.get(field)):
            errors.append(f"{field} is required")
    if data.get("mode") not in MODE_BY_PHASE[phase]:
        errors.append(f"mode {data.get('mode')!r} cannot validate phase {phase}")
    validate_timeline(data, phase, errors)
    validate_trace_audit(data, phase, errors)

    if phase == "research":
        add_research_errors(data, errors, skip_remote)
    elif phase == "plan":
        add_plan_errors(data, errors, skip_remote)
    elif phase == "orchestrate-preapproval":
        add_orientation_errors(data, errors, skip_remote)
    elif phase == "implement":
        add_implement_errors(data, errors, skip_remote)
    elif phase == "review":
        review_paths = review_changed_paths(data, errors)
        add_implement_errors(
            data,
            errors,
            skip_remote,
            visual_phase="review",
            review_paths=review_paths,
        )
        if data.get("review_dispositions_recorded") is not True:
            errors.append("review_dispositions_recorded must be true")
        if data.get("remote_checks_reported") is not True:
            errors.append("remote_checks_reported must be true")

    validate_retrospective_gate(data, phase, errors)
    if phase == "plan":
        validate_transition_gate(data, "plan", errors)
    elif phase in {"orchestrate-preapproval", "implement"}:
        validate_transition_gate(data, "implement", errors)
    elif phase == "review":
        validate_transition_gate(data, "review", errors)
    add_handoff_error(data, phase, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--phase", choices=PHASES, required=True)
    args = parser.parse_args()

    try:
        data = json.loads(args.manifest.read_text(), parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite number {value} is not allowed")
        ))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "INVALID", "errors": [str(exc)]}, indent=2))
        return 2
    if not isinstance(data, dict):
        print(json.dumps({"status": "INVALID", "errors": ["manifest must be an object"]}, indent=2))
        return 2

    errors = validate(data, args.phase, False)
    receipt = {
        "status": "VALID" if not errors else "INVALID",
        "phase": args.phase,
        "run_id": data.get("run_id"),
        "manifest": str(args.manifest),
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "remote_verification": True,
        "errors": errors,
    }
    safe_run_id = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(data.get("run_id") or "unknown"))
    receipt_path = (
        Path("/tmp")
        / "evidence-gated-delivery-receipts"
        / safe_run_id
        / f"{args.phase}.json"
    )
    receipt["receipt_path"] = str(receipt_path)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
