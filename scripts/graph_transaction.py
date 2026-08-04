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
    verify_graph_publication,
    verify_graph_authorization,
)
from plan_graph import (
    graph_draft_from_projection_bundle,
    normalize_graph_readback_evidence,
    validate_graph_readback_evidence,
)
from projection_bundle import projection_sha256, validate_projection_bundle

ActionRunner = Callable[[dict[str, Any]], dict[str, Any]]
Readback = Callable[[], dict[str, Any]]
Guard = Callable[[], list[str]]
Recorder = Callable[[dict[str, Any]], None]
LiveEvidence = Callable[[], dict[str, Any]]
AuthorizationVerifier = Callable[[dict[str, Any], dict[str, Any]], list[str]]
_MISSING = object()

GRAPH_EXTERNAL_INTENT_VERSION = "graph-external-intent/v1"
_GRAPH_INTENT_FIELDS = frozenset(
    ("schema_version", "bundle_id", "prepared_digest", "intent", "external_action")
)


def stage_graph_external_intent(
    bundle: dict[str, Any],
    draft: dict[str, Any],
    remote_state: dict[str, Any],
    *,
    target: str,
) -> dict[str, Any]:
    """Stage recoverable graph intent before any external write occurs."""

    errors = validate_projection_bundle(bundle)
    if errors:
        raise PlanProtocolError("invalid projection bundle: " + "; ".join(errors))
    projected_draft = graph_draft_from_projection_bundle(bundle)
    if projected_draft != draft:
        raise PlanProtocolError("graph draft differs from prepared projection")
    actions = planned_actions(draft, remote_state)
    staged_action_digest = projection_sha256(
        {
            "bundle_id": bundle["bundle_id"],
            "prepared_digest": bundle["prepared_digest"],
            "target": target,
            "actions": actions,
        }
    )
    return {
        "schema_version": GRAPH_EXTERNAL_INTENT_VERSION,
        "bundle_id": bundle["bundle_id"],
        "prepared_digest": bundle["prepared_digest"],
        "intent": {
            "risk_classification": "ordinary_scoped_recoverable",
            "authority_ref": bundle["bundle_id"],
            "staged_action_digest": staged_action_digest,
        },
        "external_action": {
            "target": target,
            "started_evidence": None,
            "mutation_receipt": None,
            "readback_evidence": None,
            "durable_output": None,
            "state": "not_started",
        },
    }


def validate_staged_graph_intent(
    staged: dict[str, Any],
    bundle: dict[str, Any],
    draft: dict[str, Any],
    remote_state: dict[str, Any],
) -> list[str]:
    if not isinstance(staged, dict) or set(staged) != _GRAPH_INTENT_FIELDS:
        return ["staged graph intent has unknown vocabulary"]
    errors: list[str] = []
    for field in ("bundle_id", "prepared_digest"):
        if staged.get(field) != bundle.get(field):
            errors.append(f"staged graph intent {field} mismatch")
    action = staged.get("external_action")
    if not isinstance(action, dict) or action.get("state") != "not_started":
        errors.append("staged graph intent must precede external action start")
        return errors
    expected = stage_graph_external_intent(
        bundle, draft, remote_state, target=str(action.get("target"))
    )
    if staged != expected:
        errors.append("staged graph intent digest or payload mismatch")
    return errors


def _recorded_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sanitize_evidence(value: Any) -> Any:
    """Return bounded JSON-compatible evidence with sensitive strings redacted."""

    if isinstance(value, dict):
        return {
            (
                f"[REDACTED_KEY_{index}]"
                if privacy_violations(str(key))
                else str(key)
            ): _sanitize_evidence(nested)
            for index, (key, nested) in enumerate(value.items(), start=1)
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_evidence(nested) for nested in value]
    if isinstance(value, str):
        return "[REDACTED]" if privacy_violations(value) else value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"<{type(value).__name__}>"


def _blocked_record(
    action: dict[str, Any],
    *,
    reason: str,
    mutation_result: Any = None,
    readback_state: Any = _MISSING,
    error: BaseException | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "reason": reason,
        "mutation": _sanitize_evidence(mutation_result),
    }
    if readback_state is not _MISSING:
        result["readback"] = _sanitize_evidence(readback_state)
    if error is not None:
        result["error"] = {
            "type": type(error).__name__,
            "message": _sanitize_evidence(str(error)),
        }
    return {
        **action,
        "status": "blocked",
        "result": result,
        "recorded_at": _recorded_at(),
    }


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
    authorization_verifier: AuthorizationVerifier,
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
        errors.extend(authorization_verifier(authorization, draft))
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


def publication_guard(
    draft: dict[str, Any],
    capability_receipt: dict[str, Any],
    *,
    live_evidence: LiveEvidence,
) -> Guard:
    """Recheck deterministic Plan-graph publication preconditions per write."""

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
        errors = verify_graph_publication(
            draft,
            current_login=str(observed["github_login"]),
            current_account_id=str(observed["github_account_id"]),
            current_repository=str(observed["repository"]),
            current_parent_issue_url=str(observed["parent_issue_url"]),
            capability_receipt=observed["capability_receipt"],
        )
        if observed["capability_receipt"] != capability_receipt:
            errors.append("live capability receipt differs from the publication receipt")
        for field, value in (("draft", draft), ("capability_receipt", capability_receipt)):
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
    projection_bundle: dict[str, Any] | None = None,
    staged_intent: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply only authorized missing actions, stopping at the first uncertainty."""

    if (projection_bundle is None) != (staged_intent is None):
        raise PlanProtocolError(
            "projection_bundle and staged_intent must be supplied together"
        )
    prepared_identity = None
    if projection_bundle is not None and staged_intent is not None:
        intent_errors = validate_staged_graph_intent(
            staged_intent, projection_bundle, draft, initial_remote_state
        )
        if intent_errors:
            raise PlanProtocolError("staged graph intent invalid: " + "; ".join(intent_errors))
        prepared_identity = (
            projection_bundle.get("bundle_id"),
            projection_bundle.get("prepared_digest"),
        )
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
        try:
            current = readback()
        except BaseException as exc:
            blocked = _blocked_record(
                action,
                reason="prewrite_readback_exception",
                error=exc,
            )
            records.append(blocked)
            recorder(blocked)
            raise PlanProtocolError(
                f"graph prewrite readback raised before mutation: {action}"
            ) from exc
        try:
            currently_allowed = planned_actions(draft, current)
        except BaseException as exc:
            if projection_bundle is None:
                raise
            blocked = _blocked_record(
                action,
                reason="prewrite_remote_mismatch",
                readback_state=current,
                error=exc,
            )
            records.append(blocked)
            recorder(blocked)
            raise PlanProtocolError(
                f"graph prewrite remote state conflicts with projection: {action}"
            ) from exc
        if action not in currently_allowed:
            raise PlanProtocolError(
                f"action is no longer an authorized missing subset: {action}"
            )
        attempted = {
            **action,
            "status": "attempted",
            "recorded_at": _recorded_at(),
        }
        records.append(attempted)
        recorder(attempted)
        try:
            result = runner(action)
        except BaseException as exc:
            blocked = _blocked_record(
                action,
                reason="mutation_exception",
                error=exc,
            )
            records.append(blocked)
            recorder(blocked)
            raise PlanProtocolError(
                f"graph mutation raised after durable attempt: {action}"
            ) from exc
        if not isinstance(result, dict) or not result.get("ok"):
            blocked = _blocked_record(
                action,
                reason="mutation_rejected",
                mutation_result=result,
            )
            records.append(blocked)
            recorder(blocked)
            raise PlanProtocolError(f"graph mutation failed after attempt: {action}")
        try:
            current = readback()
        except BaseException as exc:
            blocked = _blocked_record(
                action,
                reason="readback_exception",
                mutation_result=result,
                error=exc,
            )
            records.append(blocked)
            recorder(blocked)
            raise PlanProtocolError(
                f"graph mutation readback raised after successful write: {action}"
            ) from exc
        if projection_bundle is not None:
            try:
                readback_evidence = normalize_graph_readback_evidence(
                    projection_bundle, draft, current
                )
                validate_graph_readback_evidence(
                    readback_evidence, projection_bundle, draft
                )
                if prepared_identity != (
                    projection_bundle.get("bundle_id"),
                    projection_bundle.get("prepared_digest"),
                ):
                    raise PlanProtocolError(
                        "prepared projection identity mutated after external evidence"
                    )
            except BaseException as exc:
                blocked = _blocked_record(
                    action,
                    reason="readback_projection_mismatch",
                    mutation_result=result,
                    readback_state=current,
                    error=exc,
                )
                records.append(blocked)
                recorder(blocked)
                raise PlanProtocolError(
                    f"graph mutation readback projection mismatch: {action}"
                ) from exc
        try:
            remaining = planned_actions(draft, current)
        except BaseException as exc:
            blocked = _blocked_record(
                action,
                reason="readback_invalid",
                mutation_result=result,
                readback_state=current,
                error=exc,
            )
            records.append(blocked)
            recorder(blocked)
            raise PlanProtocolError(
                f"graph mutation readback was invalid after successful write: {action}"
            ) from exc
        if action in remaining:
            blocked = _blocked_record(
                action,
                reason="readback_unverified",
                mutation_result=result,
                readback_state=current,
            )
            records.append(blocked)
            recorder(blocked)
            raise PlanProtocolError(f"graph mutation lacks verified readback: {action}")
        verified = {
            **action,
            "status": "verified",
            "result": _sanitize_evidence(result),
            "recorded_at": _recorded_at(),
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
