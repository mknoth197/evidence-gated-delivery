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
import collaboration_receipts
import github_graph_adapter
import plan_phase_validation
import trace_validation
import workflow_gate_validation
import review_phase_validation
from plan_protocol import (
    PLAN_PROTOCOL_V1,
    PLAN_PROTOCOL_V2,
    WORKFLOW_VERSION_V2,
    PlanProtocolError,
    evaluate_graph_policy,
    freeze_graph_draft,
    issue_body_sha256,
    parse_tasks,
    plan_audit_callback_marker,
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


def _trace_dependencies() -> trace_validation.TraceDependencies:
    return trace_validation.TraceDependencies(
        completed_agent=completed_agent,
        agent_ids=agent_ids,
        nonempty=nonempty,
        realtime_delegated_audit_evidence=realtime_delegated_audit_evidence,
        collaboration_delegated_audit_evidence=collaboration_delegated_audit_evidence,
        agent_session_evidence=agent_session_evidence,
        persisted_delegation_role_matches=persisted_delegation_role_matches,
        timestamp=timestamp,
    )


def _plan_protocol_dependencies() -> plan_phase_validation.PlanProtocolDependencies:
    return plan_phase_validation.PlanProtocolDependencies(
        nonempty=nonempty,
        timestamp=timestamp,
        agent_ids=agent_ids,
        collaboration_delegated_audit_evidence=collaboration_delegated_audit_evidence,
        persisted_delegation_role_matches=persisted_delegation_role_matches,
        authoritative_graph_draft_errors=authoritative_graph_draft_errors,
        verify_parent_graph_authorization=(
            collaboration_receipts.verify_parent_graph_authorization
        ),
        _gh_json=_gh_json,
        _live_graph_capabilities=_live_graph_capabilities,
        _remote_graph_state=_remote_graph_state,
        _remote_workflow_graph_artifacts=_remote_workflow_graph_artifacts,
    )


def _plan_gate_dependencies() -> plan_phase_validation.PlanGateDependencies:
    return plan_phase_validation.PlanGateDependencies(
        PLAN_HEADINGS=PLAN_HEADINGS,
        add_research_errors=add_research_errors,
        validate_plan_identity_and_evidence=validate_plan_identity_and_evidence,
        agent_ids=agent_ids,
        completed_agent=completed_agent,
        nonempty=nonempty,
        validate_image_receipts=validate_image_receipts,
        finite_number=finite_number,
        generated_image_file=generated_image_file,
        issue_url=issue_url,
        require_remote_issue=require_remote_issue,
        validate_plan_protocol_evidence=validate_plan_protocol_evidence,
        validate_disposition=validate_disposition,
        reference_present=reference_present,
        durable_image_url=durable_image_url,
        remote_image_sha256=remote_image_sha256,
        markdown_section=markdown_section,
    )


def _review_dependencies() -> review_phase_validation.ReviewDependencies:
    return review_phase_validation.ReviewDependencies(
        add_plan_errors=add_plan_errors,
        nonempty=nonempty,
        pr_url=pr_url,
        github_pr_oids=github_pr_oids,
        completed_agent=completed_agent,
        collaboration_delegated_audit_evidence=collaboration_delegated_audit_evidence,
        persisted_delegation_role_matches=persisted_delegation_role_matches,
        timestamp=timestamp,
        agent_ids=agent_ids,
        github_readback=github_readback,
    )


def _workflow_gate_dependencies() -> workflow_gate_validation.WorkflowGateDependencies:
    return workflow_gate_validation.WorkflowGateDependencies(
        nonempty=nonempty,
        timestamp=timestamp,
        finite_number=finite_number,
        collaboration_delegated_audit_evidence=collaboration_delegated_audit_evidence,
        agent_session_evidence=agent_session_evidence,
        persisted_delegation_role_matches=persisted_delegation_role_matches,
        RETROSPECTIVE_RUBRIC=RETROSPECTIVE_RUBRIC,
    )


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
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


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
    if phase in {"implement", "review"}:
        required += ["implement_completed_at"]
    if phase == "review":
        required += ["review_completed_at"]
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
    return collaboration_receipts.agent_session_evidence(agent_id)


def realtime_delegated_audit_evidence(
    data: dict[str, Any], audit: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    return collaboration_receipts.realtime_delegated_audit_evidence(data, audit)


def collaboration_delegated_audit_evidence(
    data: dict[str, Any], audit: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    return collaboration_receipts.collaboration_delegated_audit_evidence(
        data, audit, session_reader=agent_session_evidence
    )


def persisted_delegation_role_matches(arguments: Any, expected_marker: str) -> bool:
    return collaboration_receipts.persisted_delegation_role_matches(
        arguments, expected_marker
    )


def authoritative_graph_draft_errors(
    draft: Any,
    parent_issue_url: str,
    repository: str,
    tasks: list[dict[str, Any]],
) -> list[str]:
    """Bind a supplied graph draft to the canonical tasks parsed from Plan authority."""

    errors = validate_graph_draft(draft)
    try:
        expected = freeze_graph_draft(parent_issue_url, repository, tasks)
    except PlanProtocolError as exc:
        errors.append(f"authoritative graph draft cannot be derived: {exc}")
        return errors
    if draft != expected:
        errors.append("graph_draft does not match authoritative Plan tasks")
    return errors


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
    trace_validation.validate_trace_audit(
        data, phase, errors, deps=_trace_dependencies()
    )


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
    return github_graph_adapter.gh_json(arguments)


def _remote_graph_state(
    implementation_url: str,
) -> tuple[dict[str, Any] | None, str | None]:
    return github_graph_adapter.remote_graph_state(
        implementation_url, reader=_gh_json
    )


def _remote_workflow_graph_artifacts(
    implementation_url: str,
) -> tuple[list[str] | None, str | None]:
    return github_graph_adapter.remote_workflow_graph_artifacts(
        implementation_url, reader=_gh_json
    )


def _live_graph_capabilities() -> tuple[dict[str, Any] | None, str | None]:
    return github_graph_adapter.live_graph_capabilities()


def validate_plan_protocol_evidence(
    data: dict[str, Any],
    implementation_body: str,
    errors: list[str],
    *,
    skip_remote: bool,
) -> None:
    plan_phase_validation.validate_plan_protocol_evidence(
        data,
        implementation_body,
        errors,
        skip_remote=skip_remote,
        deps=_plan_protocol_dependencies(),
    )


def add_plan_errors(
    data: dict[str, Any],
    errors: list[str],
    skip_remote: bool,
    visual_phase: str = "plan",
    review_paths: list[str] | None = None,
) -> None:
    plan_phase_validation.add_plan_errors(
        data,
        errors,
        skip_remote,
        visual_phase,
        review_paths,
        deps=_plan_gate_dependencies(),
    )


def add_orientation_errors(data: dict[str, Any], errors: list[str], skip_remote: bool) -> None:
    review_phase_validation.add_orientation_errors(
        data, errors, skip_remote, deps=_review_dependencies()
    )


def review_changed_paths(
    data: dict[str, Any], errors: list[str]
) -> list[str] | None:
    return review_phase_validation.review_changed_paths(
        data, errors, deps=_review_dependencies()
    )


def validate_reviewer(
    data: dict[str, Any], entry: Any, label: str, errors: list[str], *,
    expected_marker: str | None = None,
) -> str | None:
    return review_phase_validation.validate_reviewer(
        data, entry, label, errors, expected_marker=expected_marker,
        deps=_review_dependencies(),
    )


def add_implement_errors(
    data: dict[str, Any], errors: list[str], skip_remote: bool,
    visual_phase: str = "implement", review_paths: list[str] | None = None,
) -> None:
    review_phase_validation.add_implement_errors(
        data, errors, skip_remote, visual_phase, review_paths,
        deps=_review_dependencies(),
    )


def add_handoff_error(data: dict[str, Any], phase: str, errors: list[str]) -> None:
    workflow_gate_validation.add_handoff_error(
        data, phase, errors, deps=_workflow_gate_dependencies()
    )


def transition_judge_excluded_ids(
    data: dict[str, Any], current_judgment: dict[str, Any]
) -> set[str]:
    return workflow_gate_validation.transition_judge_excluded_ids(
        data, current_judgment, deps=_workflow_gate_dependencies()
    )


def validate_transition_gate(data: dict[str, Any], target_phase: str, errors: list[str]) -> None:
    workflow_gate_validation.validate_transition_gate(
        data, target_phase, errors, deps=_workflow_gate_dependencies()
    )


def validate_retrospective_gate(data: dict[str, Any], phase: str, errors: list[str]) -> None:
    workflow_gate_validation.validate_retrospective_gate(
        data, phase, errors, deps=_workflow_gate_dependencies()
    )


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
    if not isinstance(data.get("phase_timeline"), dict):
        return errors
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
