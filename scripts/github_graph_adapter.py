#!/usr/bin/env python3
"""GitHub CLI adapters for protected Plan graph read-back."""
from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from plan_protocol import issue_body_sha256

def gh_json(arguments: list[str]) -> tuple[dict[str, Any] | None, str | None]:
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


def remote_graph_state(
    implementation_url: str, *, reader=gh_json,
) -> tuple[dict[str, Any] | None, str | None]:
    match = re.fullmatch(
        r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)",
        implementation_url,
    )
    if match is None:
        return None, "implementation issue URL cannot identify graph repository and parent"
    owner, repository_name, parent_number = match.groups()
    repository = f"{owner}/{repository_name}"
    parent, error = reader(
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
        child, child_error = reader(
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


def remote_workflow_graph_artifacts(
    implementation_url: str, *, reader=gh_json,
) -> tuple[list[str] | None, str | None]:
    """Return workflow-owned child URLs attached to a NO_GRAPH parent."""
    match = re.fullmatch(
        r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)",
        implementation_url,
    )
    if match is None:
        return None, "implementation issue URL cannot identify graph repository and parent"
    owner, repository_name, parent_number = match.groups()
    repository = f"{owner}/{repository_name}"
    parent, error = reader(
        [
            "issue",
            "view",
            parent_number,
            "--repo",
            repository,
            "--json",
            "subIssues",
        ]
    )
    if error:
        return None, error
    assert parent is not None
    raw_children = parent.get("subIssues")
    if not isinstance(raw_children, list):
        return None, "parent issue read-back lacks subIssues"
    workflow_children: list[str] = []
    for child_summary in raw_children:
        if not isinstance(child_summary, dict) or not isinstance(
            child_summary.get("number"), int
        ):
            return None, "parent subIssues contains a malformed child"
        child, child_error = reader(
            [
                "issue",
                "view",
                str(child_summary["number"]),
                "--repo",
                repository,
                "--json",
                "url,body",
            ]
        )
        if child_error:
            return None, child_error
        assert child is not None
        if re.search(
            r"<!-- evidence-gated-delivery-task:T-\d{3} -->",
            str(child.get("body", "")),
        ):
            workflow_children.append(str(child.get("url", "")))
    return workflow_children, None


def live_graph_capabilities() -> tuple[dict[str, Any] | None, str | None]:
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
