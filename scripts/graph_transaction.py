#!/usr/bin/env python3
"""Fail-closed coordinator for authorized GitHub issue-graph publication."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from plan_protocol import (
    PlanProtocolError,
    privacy_violations,
    reconcile_graph_state,
    verify_graph_authorization,
)

ActionRunner = Callable[[dict[str, Any]], dict[str, Any]]
Readback = Callable[[], dict[str, Any]]
Guard = Callable[[], list[str]]
Recorder = Callable[[dict[str, Any]], None]
LiveEvidence = Callable[[], dict[str, Any]]


def planned_actions(
    draft: dict[str, Any], remote_state: dict[str, Any]
) -> list[dict[str, Any]]:
    reconciliation = reconcile_graph_state(draft, remote_state)
    if reconciliation["classification"] == "CONFLICT":
        raise PlanProtocolError(f"graph conflict: {reconciliation['reasons']}")
    actions = [
        {"kind": "create_child", "key": task_id}
        for task_id in reconciliation.get("missing_children", [])
    ]
    actions.extend(
        {
            "kind": "add_blocked_by",
            "key": f"{edge['blocked']}<-{edge['blocked_by']}",
        }
        for edge in reconciliation.get("missing_edges", [])
    )
    return actions


def authorization_guard(
    authorization: dict[str, Any],
    draft: dict[str, Any],
    capability_receipt: dict[str, Any],
    *,
    live_evidence: LiveEvidence,
) -> Guard:
    def check() -> list[str]:
        observed = live_evidence()
        required = (
            "github_login",
            "github_account_id",
            "repository",
            "parent_issue_url",
            "capability_receipt",
        )
        missing = [field for field in required if field not in observed]
        if missing:
            return [f"live graph evidence is missing {field}" for field in missing]
        errors = verify_graph_authorization(
            authorization,
            draft,
            current_login=str(observed["github_login"]),
            current_account_id=str(observed["github_account_id"]),
            current_repository=str(observed["repository"]),
            current_parent_issue_url=str(observed["parent_issue_url"]),
            capability_receipt=observed["capability_receipt"],
        )
        if observed["capability_receipt"] != capability_receipt:
            errors.append(
                "live capability receipt differs from the authorized capability receipt"
            )
        for field, value in (
            ("authorization", authorization),
            ("draft", draft),
            ("capability_receipt", capability_receipt),
        ):
            errors.extend(
                f"privacy sentinel: {violation}"
                for violation in privacy_violations(value, f"$.{field}")
            )
        return errors

    return check


def execute_transaction(
    draft: dict[str, Any],
    initial_remote_state: dict[str, Any],
    *,
    guard: Guard,
    runner: ActionRunner,
    readback: Readback,
    recorder: Recorder,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply only authorized missing actions, stopping at the first uncertainty."""

    actions = planned_actions(draft, initial_remote_state)
    records: list[dict[str, Any]] = []
    current = initial_remote_state
    for action in actions:
        guard_errors = guard()
        if guard_errors:
            raise PlanProtocolError(
                "authorization recheck failed: " + "; ".join(guard_errors)
            )
        # Reconcile immediately before every write so concurrent drift fails closed.
        current = readback()
        currently_allowed = planned_actions(draft, current)
        if action not in currently_allowed:
            raise PlanProtocolError(
                f"action is no longer an authorized missing subset: {action}"
            )
        attempted = {
            **action,
            "status": "attempted",
            "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        records.append(attempted)
        recorder(attempted)
        try:
            result = runner(action)
        except BaseException as exc:
            blocked = {
                **action,
                "status": "blocked",
                "result": {"error": type(exc).__name__, "message": str(exc)},
                "recorded_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
            records.append(blocked)
            recorder(blocked)
            raise PlanProtocolError(
                f"graph mutation raised after durable attempt: {action}"
            ) from exc
        if not isinstance(result, dict) or not result.get("ok"):
            blocked = {
                **action,
                "status": "blocked",
                "result": result,
                "recorded_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
            records.append(blocked)
            recorder(blocked)
            raise PlanProtocolError(f"graph mutation failed after attempt: {action}")
        current = readback()
        if action in planned_actions(draft, current):
            raise PlanProtocolError(f"graph mutation lacks verified readback: {action}")
        verified = {
            **action,
            "status": "verified",
            "result": result,
            "recorded_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        records.append(verified)
        recorder(verified)
    final = reconcile_graph_state(draft, current)
    if final["classification"] != "EXACT_MATCH":
        raise PlanProtocolError(f"transaction ended without exact graph: {final}")
    return records, current


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PlanProtocolError(f"{path} must contain a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", type=Path, required=True)
    parser.add_argument("--remote-state", type=Path, required=True)
    parser.add_argument(
        "--print-actions",
        action="store_true",
        help="Print the exact authorized-missing action plan without mutating GitHub",
    )
    args = parser.parse_args()
    try:
        draft = _load(args.draft)
        remote = _load(args.remote_state)
        actions = planned_actions(draft, remote)
        if not args.print_actions:
            raise PlanProtocolError(
                "direct CLI mutation is disabled; invoke execute_transaction with "
                "authenticated guard, runner, and live readback adapters"
            )
        print(json.dumps({"status": "DRY_RUN", "actions": actions}, indent=2))
        return 0
    except (OSError, json.JSONDecodeError, PlanProtocolError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
