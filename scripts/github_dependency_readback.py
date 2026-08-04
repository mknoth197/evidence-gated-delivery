#!/usr/bin/env python3
"""Live GitHub read-back adapters for dependency-readiness predicates."""

from __future__ import annotations

import base64
import json
import re
import subprocess
from typing import Any
from urllib.parse import quote


def _gh_json(arguments: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        result = subprocess.run(
            ["gh", *arguments], check=False, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, result.stderr.strip() or f"gh exited {result.returncode}"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return None, str(exc)
    if not isinstance(payload, dict):
        return None, "GitHub response must be an object"
    return payload, None


def dependency_authority_reader(
    url: str,
) -> tuple[dict[str, Any] | None, str | None]:
    payload, error = _gh_json(["issue", "view", url, "--json", "url,state,body"])
    if error or payload is None:
        return None, error or "issue read-back failed"
    if payload.get("url") != url:
        return None, f"remote URL mismatch: {payload.get('url')}"
    if not isinstance(payload.get("state"), str) or not isinstance(payload.get("body"), str):
        return None, "remote authority lacks state or body"
    return {"state": payload["state"], "body": payload["body"]}, None


def dependency_interface_reader(
    repository: str, commit_sha: str, interface_path: str
) -> tuple[bytes | None, str | None]:
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
        return None, "repository must use owner/name form"
    repo, error = _gh_json(["api", f"repos/{repository}"])
    if error or repo is None:
        return None, error or "repository read-back failed"
    default_branch = repo.get("default_branch")
    if not isinstance(default_branch, str) or not default_branch:
        return None, "repository default branch is missing"
    comparison, error = _gh_json(
        ["api", f"repos/{repository}/compare/{commit_sha}...{quote(default_branch, safe='')}"]
    )
    if error or comparison is None:
        return None, error or "default-branch comparison failed"
    if comparison.get("status") not in {"ahead", "identical"}:
        return None, "pinned commit is not merged into the default branch"
    encoded_path = quote(interface_path.lstrip("/"), safe="/")
    payload, error = _gh_json(
        ["api", f"repos/{repository}/contents/{encoded_path}?ref={commit_sha}"]
    )
    if error or payload is None:
        return None, error or "interface content read-back failed"
    if payload.get("type") != "file" or payload.get("encoding") != "base64":
        return None, "interface authority is not a base64 file"
    try:
        encoded = "".join(str(payload.get("content", "")).split())
        return base64.b64decode(encoded, validate=True), None
    except (TypeError, ValueError) as exc:
        return None, f"invalid interface content: {exc}"
