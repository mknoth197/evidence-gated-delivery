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
    else:
        evidence, session_error = agent_session_evidence(matching[0]["agent_id"])
    if session_error:
        errors.append(f"{required_phase} trace auditor session verification failed: {session_error}")
        return
    assert evidence is not None
    final_message = evidence["final_message"]
    session_meta = evidence["session_meta"]
    if not realtime:
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
        evidence.get("delegation_started_at") if realtime else session_meta.get("timestamp")
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


def validate_plan_identity_and_evidence(data: dict[str, Any], errors: list[str]) -> None:
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
    if not isinstance(grounding, list) or not grounding:
        errors.append("visual_grounding must contain a current product-shell observation")
    else:
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
        "final-mockup-publication": "durable_mockup_publication",
        "plan-issue-readback": "github_issue_readback",
        "research-issue-readback": "github_issue_readback",
    }
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


def add_plan_errors(data: dict[str, Any], errors: list[str], skip_remote: bool) -> None:
    research_body = add_research_errors(data, errors, skip_remote)
    validate_plan_identity_and_evidence(data, errors)
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
            for field in ("concept", "visual_brief"):
                if not nonempty(contestant.get(field)):
                    errors.append(f"contestants[{index}].{field} is required")
            if nonempty(contestant.get("concept")):
                concepts.append(" ".join(contestant["concept"].lower().split()))
    if len(candidate_ids) != 3 or len(set(candidate_ids)) != 3:
        errors.append("exactly three unique candidate IDs are required")
    if len(concepts) != 3 or len(set(concepts)) != 3:
        errors.append("contestant concepts must be distinct")
    candidate_set = set(candidate_ids)

    validate_image_receipts(data.get("contestant_images"), candidate_set, errors, "contestant_images")

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
    if not isinstance(semantic_reviews, list) or len(semantic_reviews) != 3:
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
    validate_image_receipts(final_iterations, None, errors, "final_image_iterations")
    if not isinstance(final_iterations, list) or not final_iterations:
        errors.append("at least one final ImageGen iteration is required")
    elif isinstance(final_iterations[-1], dict):
        final_confidence = final_iterations[-1].get("confidence")
        if not finite_number(final_confidence, 7, 10):
            errors.append("final confidence must be at least 7")
        for index, iteration in enumerate(final_iterations[:-1]):
            if not isinstance(iteration, dict) or not finite_number(iteration.get("confidence"), 1, 6.999):
                errors.append(f"final_image_iterations[{index}] must record sub-7 confidence")

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
    if contestant_paths & final_paths or contestant_calls & final_calls:
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
        if not nonempty(final_sha) or final_sha not in implementation_body:
            errors.append("implementation issue does not bind the final image SHA-256")


def add_orientation_errors(data: dict[str, Any], errors: list[str], skip_remote: bool) -> None:
    add_plan_errors(data, errors, skip_remote)
    if data.get("orientation_complete") is not True:
        errors.append("orientation_complete must be true")
    if data.get("approval_requested") is not True:
        errors.append("approval_requested must be true")
    if data.get("approval_granted") is True:
        errors.append("approval_granted must still be false at the preapproval gate")
    if data.get("no_mutation_before_approval") is not True:
        errors.append("no_mutation_before_approval must be true")


def validate_reviewer(entry: Any, label: str, errors: list[str]) -> str | None:
    if not completed_agent(entry):
        errors.append(f"{label} must be a completed agent receipt")
        return None
    return entry["agent_id"].strip()


def add_implement_errors(data: dict[str, Any], errors: list[str], skip_remote: bool) -> None:
    add_plan_errors(data, errors, skip_remote)
    if data.get("orientation_complete") is not True:
        errors.append("orientation_complete must be true")
    if data.get("approval_requested") is not True:
        errors.append("approval_requested must be true")
    if data.get("approval_granted") is not True:
        errors.append("approval_granted must be true")
    approval = data.get("approval_evidence")
    approval_at = timestamp(approval.get("received_at")) if isinstance(approval, dict) else None
    mutation_at = timestamp(data.get("first_mutation_at"))
    if not isinstance(approval, dict) or not nonempty(approval.get("quote")) or approval_at is None:
        errors.append("approval_evidence must contain the user's exact quote and timestamp")
    if mutation_at is None:
        errors.append("first_mutation_at must be an ISO-8601 timestamp")
    elif approval_at is not None and mutation_at < approval_at:
        errors.append("first mutation occurred before explicit approval")
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

    test_id = validate_reviewer(data.get("test_reviewer"), "test_reviewer", errors)
    acceptance_id = validate_reviewer(data.get("acceptance_reviewer"), "acceptance_reviewer", errors)
    all_prior_ids = set(agent_ids(data.get("contestants"))) | set(agent_ids(data.get("judges")))
    all_ids = worker_ids + [value for value in (test_id, acceptance_id) if value]
    if len(all_ids) != len(set(all_ids)):
        errors.append("implementation workers and reviewers must have unique agent IDs")
    if set(all_ids) & all_prior_ids:
        errors.append("implementation workers/reviewers must be fresh from tournament agents")

    if data.get("unexplained_mockup_gaps") != 0:
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
        add_implement_errors(data, errors, skip_remote)
        if data.get("review_dispositions_recorded") is not True:
            errors.append("review_dispositions_recorded must be true")
        if data.get("remote_checks_reported") is not True:
            errors.append("remote_checks_reported must be true")

    validate_retrospective_gate(data, phase, errors)
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
