#!/usr/bin/env python3
"""Implement-orientation and Review phase validation."""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any

def add_orientation_errors(data: dict[str, Any], errors: list[str], skip_remote: bool, *, deps: Any) -> None:
    deps.add_plan_errors(data, errors, skip_remote, visual_phase="implement-orientation")
    if data.get("orientation_complete") is not True:
        errors.append("orientation_complete must be true")
    if data.get("no_mutation_before_approval") is not True:
        errors.append("no_mutation_before_approval must be true")


def review_changed_paths(
    data: dict[str, Any], errors: list[str], *, deps: Any
) -> list[str] | None:
    root = Path(data.get("repo_root", ""))
    starting_commit = data.get("starting_commit")
    if not root.is_dir() or not (root / ".git").exists():
        errors.append("Review requires an existing repository worktree for actual-diff binding")
        return None
    if not deps.nonempty(starting_commit) or not re.fullmatch(
        r"[0-9a-fA-F]{40}", starting_commit
    ):
        errors.append("Review requires a full starting_commit for actual-diff binding")
        return None
    pull_url = data.get("pull_request_url")
    if not deps.pr_url(pull_url):
        errors.append("Review requires a GitHub pull request URL for actual-diff binding")
        return None
    remote, remote_error = deps.github_pr_oids(pull_url)
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
    deps: Any,
) -> str | None:
    if not deps.completed_agent(entry):
        errors.append(f"{label} must be a completed agent receipt")
        return None
    if expected_marker is not None:
        if entry.get("receipt_kind") != "collaboration_delegated":
            errors.append(f"{label} must use collaboration_delegated provenance")
            return None
        evidence, session_error = deps.collaboration_delegated_audit_evidence(data, entry)
        if session_error:
            errors.append(f"{label} session verification failed: {session_error}")
            return None
        assert evidence is not None
        callback = evidence["final_message"]
        if not deps.persisted_delegation_role_matches(
            evidence.get("delegation_arguments"), expected_marker
        ):
            errors.append(
                f"{label} persisted delegation lacks the required role marker"
            )
        if entry.get("result") != callback:
            errors.append(f"{label}.result does not match authenticated callback")
        if entry.get("result_sha256") != hashlib.sha256(callback.encode()).hexdigest():
            errors.append(f"{label}.result_sha256 does not match authenticated callback")
        if deps.timestamp(entry.get("started_at")) != deps.timestamp(
            evidence.get("delegation_started_at")
        ):
            errors.append(f"{label}.started_at does not match delegation")
        if deps.timestamp(entry.get("completed_at")) != deps.timestamp(
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
    *,
    deps: Any,
) -> None:
    deps.add_plan_errors(
        data,
        errors,
        skip_remote,
        visual_phase=visual_phase,
        review_paths=review_paths,
    )
    if data.get("orientation_complete") is not True:
        errors.append("orientation_complete must be true")
    mutation_at = deps.timestamp(data.get("first_mutation_at"))
    if mutation_at is None:
        errors.append("first_mutation_at must be an ISO-8601 timestamp")
    if data.get("no_mutation_before_approval") is not True:
        errors.append("no_mutation_before_approval must be true")

    workers = data.get("implementation_workers")
    worker_ids = deps.agent_ids(workers)
    if not worker_ids:
        errors.append("at least one completed implementation worker is required")
    if isinstance(workers, list):
        for index, worker in enumerate(workers):
            if not deps.completed_agent(worker):
                errors.append(f"implementation_workers[{index}] must be a completed agent receipt")
                continue
            ownership = worker.get("ownership")
            if not isinstance(ownership, list) or not any(deps.nonempty(v) for v in ownership):
                errors.append(f"implementation_workers[{index}].ownership is required")
            if not deps.nonempty(worker.get("handoff")):
                errors.append(f"implementation_workers[{index}].handoff is required")

    test_id = validate_reviewer(
        data,
        data.get("test_reviewer"),
        "test_reviewer",
        errors,
        expected_marker="Test-Coverage Reviewer",
        deps=deps,
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
            deps=deps,
        )
    all_prior_ids = set(deps.agent_ids(data.get("contestants"))) | set(deps.agent_ids(data.get("judges")))
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
                or not deps.nonempty(gate.get("name"))
                or gate.get("status") not in {"passed", "skipped"}
                or not deps.nonempty(gate.get("evidence"))
            ):
                errors.append(f"quality_gates[{index}] is invalid")
            elif gate["status"] == "passed":
                passed = True
        if not passed:
            errors.append("at least one quality gate must have passed")

    pull_url = data.get("pull_request_url")
    if not deps.pr_url(pull_url):
        errors.append("pull_request_url must be a GitHub pull request URL")
    elif not skip_remote:
        _, error = deps.github_readback(pull_url, "pr")
        if error:
            errors.append(f"pull_request_url remote read-back failed: {error}")
