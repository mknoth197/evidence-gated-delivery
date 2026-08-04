#!/usr/bin/env python3
"""Frozen Plan graph authorization, reconciliation, and proof."""
from __future__ import annotations

import re
from typing import Any, Mapping

from plan_protocol_core import PlanProtocolError, issue_body_sha256, sha256_json
from plan_events import _validate_iso8601
from plan_tasks import (
    authority_text,
    parse_tasks,
    present_projection_slot,
    projection_payload,
)

GRAPH_DRAFT_PROJECTION_VERSION = "graph-draft-projection/v1"
GRAPH_READBACK_EVIDENCE_VERSION = "graph-readback-evidence/v1"
_GRAPH_DRAFT_PAYLOAD_FIELDS = frozenset(
    ("adapter_version", "input_digest", "graph_draft")
)
_GRAPH_READBACK_EVIDENCE_FIELDS = frozenset(
    (
        "schema_version",
        "bundle_id",
        "prepared_digest",
        "input_digest",
        "remote_state",
        "remote_state_digest",
        "reconciliation",
    )
)


def graph_draft_projection_adapter(*, parent_issue_url: str, repository: str):
    """Return a graph-draft adapter bound to one kernel authority digest."""

    def project(
        authority_bytes: bytes,
        authority_digest: str,
        versions: Mapping[str, str],
    ) -> dict[str, Any]:
        draft = freeze_graph_draft(
            parent_issue_url,
            repository,
            parse_tasks(authority_text(authority_bytes)),
        )
        payload = {
            "adapter_version": GRAPH_DRAFT_PROJECTION_VERSION,
            "input_digest": authority_digest,
            "graph_draft": draft,
        }
        return {
            "authority_digest": authority_digest,
            "versions": dict(versions),
            "slot": present_projection_slot(payload, GRAPH_DRAFT_PROJECTION_VERSION),
        }

    return project


def graph_draft_from_projection_bundle(
    bundle: Mapping[str, Any], slot_name: str = "graph_draft"
) -> dict[str, Any]:
    payload = projection_payload(
        bundle,
        slot_name,
        projection_version=GRAPH_DRAFT_PROJECTION_VERSION,
        payload_fields=_GRAPH_DRAFT_PAYLOAD_FIELDS,
    )
    draft = payload["graph_draft"]
    errors = validate_graph_draft(draft)
    if errors:
        raise PlanProtocolError("invalid projected graph draft: " + "; ".join(errors))
    return draft


def normalize_graph_readback_evidence(
    bundle: Mapping[str, Any],
    draft: Mapping[str, Any],
    remote_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind later remote evidence without changing the prepared bundle identity."""

    from projection_bundle import validate_projection_bundle

    bundle_errors = validate_projection_bundle(dict(bundle))
    if bundle_errors:
        raise PlanProtocolError("invalid projection bundle: " + "; ".join(bundle_errors))
    authority = bundle.get("authority")
    input_digest = authority.get("bytes_digest") if isinstance(authority, Mapping) else None
    if not isinstance(input_digest, str):
        raise PlanProtocolError("projection bundle lacks authority digest")
    normalized = {
        "children": list(remote_state.get("children", [])),
        "edges": list(remote_state.get("edges", [])),
    }
    reconciliation = reconcile_graph_state(dict(draft), normalized)
    return {
        "schema_version": GRAPH_READBACK_EVIDENCE_VERSION,
        "bundle_id": bundle.get("bundle_id"),
        "prepared_digest": bundle.get("prepared_digest"),
        "input_digest": input_digest,
        "remote_state": normalized,
        "remote_state_digest": sha256_json(normalized),
        "reconciliation": reconciliation,
    }


def validate_graph_readback_evidence(
    evidence: Mapping[str, Any],
    bundle: Mapping[str, Any],
    draft: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if set(evidence) != _GRAPH_READBACK_EVIDENCE_FIELDS:
        raise PlanProtocolError("graph read-back evidence has unknown vocabulary")
    expected = {
        "schema_version": GRAPH_READBACK_EVIDENCE_VERSION,
        "bundle_id": bundle.get("bundle_id"),
        "prepared_digest": bundle.get("prepared_digest"),
        "input_digest": (
            bundle.get("authority", {}).get("bytes_digest")
            if isinstance(bundle.get("authority"), Mapping)
            else None
        ),
    }
    for field, value in expected.items():
        if evidence.get(field) != value:
            raise PlanProtocolError(f"graph read-back evidence {field} mismatch")
    remote_state = evidence["remote_state"]
    if not isinstance(remote_state, dict) or evidence.get("remote_state_digest") != sha256_json(
        remote_state
    ):
        raise PlanProtocolError("projected graph read-back digest mismatch")
    if draft is not None:
        expected_reconciliation = reconcile_graph_state(dict(draft), remote_state)
        if evidence.get("reconciliation") != expected_reconciliation:
            raise PlanProtocolError("graph read-back reconciliation mismatch")
    return dict(evidence)


def graph_child_body_sha256(body: str) -> str:
    """Hash a child issue's semantic payload, not its transport terminator.

    GitHub issue creation and read-back may disagree about terminal newlines.  The
    stable marker and all meaningful Markdown bytes remain hash-bound; only the
    number of newline terminators is normalized so a transport detail cannot turn
    an otherwise exact authorized graph into a conflict.
    """

    if not isinstance(body, str):
        raise PlanProtocolError("graph child body must be a string")
    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    return issue_body_sha256(normalized.rstrip("\n") + "\n")


def graph_edges(tasks: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"blocked": task["task_id"], "blocked_by": dependency}
        for task in tasks
        for dependency in task["depends_on"]
    ]


def freeze_graph_draft(
    parent_issue_url: str, repository: str, tasks: list[dict[str, Any]]
) -> dict[str, Any]:
    if not parent_issue_url.startswith("https://github.com/"):
        raise PlanProtocolError("parent issue URL must be an HTTPS GitHub URL")
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
        raise PlanProtocolError("repository must use owner/name form")
    children = [
        _graph_child(task)
        for task in tasks
    ]
    draft = {
        "schema_version": "graph-draft/v1",
        "parent_issue_url": parent_issue_url,
        "repository": repository,
        "children": children,
        "edges": graph_edges(tasks),
    }
    draft["draft_sha256"] = sha256_json(draft)
    return draft


def _graph_child(task: dict[str, Any]) -> dict[str, Any]:
    marker = f"<!-- evidence-gated-delivery-task:{task['task_id']} -->"
    body = f"{marker}\n\n{task['body']}"
    return {
        "task_id": task["task_id"],
        "stable_marker": marker,
        "title": task["title"],
        "body": body,
        "body_sha256": graph_child_body_sha256(body),
    }


def validate_graph_draft(draft: Any) -> list[str]:
    if not isinstance(draft, dict):
        return ["graph draft must be an object"]
    errors: list[str] = []
    if draft.get("schema_version") != "graph-draft/v1":
        errors.append("graph draft schema_version is unsupported")
    children = draft.get("children")
    edges = draft.get("edges")
    if not isinstance(children, list) or not children:
        errors.append("graph draft children must be a non-empty array")
        children = []
    if not isinstance(edges, list):
        errors.append("graph draft edges must be an array")
        edges = []
    ids: list[str] = []
    for index, child in enumerate(children):
        if not isinstance(child, dict):
            errors.append(f"graph draft child {index} must be an object")
            continue
        task_id = child.get("task_id")
        ids.append(task_id)
        if child.get("stable_marker") != f"<!-- evidence-gated-delivery-task:{task_id} -->":
            errors.append(f"graph draft child {task_id} has invalid stable marker")
        try:
            if graph_child_body_sha256(child.get("body", "")) != child.get("body_sha256"):
                errors.append(f"graph draft child {task_id} body hash mismatch")
        except PlanProtocolError:
            errors.append(f"graph draft child {task_id} body must be text")
    normalized_ids = [str(value) for value in ids]
    if len(normalized_ids) != len(set(normalized_ids)):
        errors.append("graph draft task IDs must be unique")
    expected_edge_keys: set[tuple[str, str]] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            errors.append("graph draft edge must be an object")
            continue
        pair = (str(edge.get("blocked")), str(edge.get("blocked_by")))
        if pair in expected_edge_keys:
            errors.append("graph draft edges must be unique")
        expected_edge_keys.add(pair)
        if any(task_id not in normalized_ids for task_id in pair):
            errors.append("graph draft edge references an unknown task")
    try:
        expected_hash = sha256_json({key: value for key, value in draft.items() if key != "draft_sha256"})
        if draft.get("draft_sha256") != expected_hash:
            errors.append("graph draft SHA-256 mismatch")
    except (TypeError, ValueError):
        errors.append("graph draft contains non-JSON content")
    return errors


def verify_graph_authorization(
    authorization: Any,
    draft: dict[str, Any],
    *,
    current_login: str,
    current_account_id: str,
    current_repository: str,
    current_parent_issue_url: str,
    capability_receipt: dict[str, Any],
) -> list[str]:
    errors = validate_graph_draft(draft)
    if not isinstance(authorization, dict):
        return errors + ["graph authorization must be an object"]
    if errors:
        return errors
    if not isinstance(capability_receipt, dict):
        return ["capability receipt must be an object"]
    required_matches = {
        "github_login": current_login,
        "github_account_id": current_account_id,
        "repository": current_repository,
        "parent_issue_url": current_parent_issue_url,
        "draft_sha256": draft.get("draft_sha256"),
        "capability_receipt_sha256": sha256_json(capability_receipt),
        "child_body_sha256s": [child["body_sha256"] for child in draft.get("children", [])],
        "edges": draft.get("edges", []),
    }
    for field, expected in required_matches.items():
        if authorization.get(field) != expected:
            errors.append(f"graph authorization {field} does not match current transaction")
    if current_repository != draft.get("repository"):
        errors.append("current repository does not match frozen draft")
    if current_parent_issue_url != draft.get("parent_issue_url"):
        errors.append("current parent issue does not match frozen draft")
    capability_matches = {
        "github_login": current_login,
        "github_account_id": current_account_id,
        "repository": current_repository,
        "parent_issue_url": current_parent_issue_url,
        "native_parent_supported": True,
        "blocking_supported": True,
        "readback_supported": True,
    }
    for field, expected in capability_matches.items():
        if capability_receipt.get(field) != expected:
            errors.append(f"capability receipt {field} is missing or stale")
    evidence = authorization.get("authorization_evidence")
    if not isinstance(evidence, dict):
        errors.append("graph authorization requires authenticated authorization evidence")
    else:
        expected_evidence = {
            "receipt_kind": "authenticated_parent_user_message",
            "draft_sha256": draft.get("draft_sha256"),
        }
        for field, expected in expected_evidence.items():
            if evidence.get(field) != expected:
                errors.append(f"graph authorization evidence {field} mismatch")
        if not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("message_sha256"))):
            errors.append("graph authorization evidence message_sha256 is invalid")
        try:
            _validate_iso8601(
                evidence.get("authorized_at"),
                "graph authorization evidence authorized_at",
            )
        except PlanProtocolError as exc:
            errors.append(str(exc))
    try:
        _validate_iso8601(authorization.get("authorized_at"), "graph authorization authorized_at")
    except PlanProtocolError as exc:
        errors.append(str(exc))
    return errors


def verify_graph_publication(
    draft: dict[str, Any],
    *,
    current_login: str,
    current_account_id: str,
    current_repository: str,
    current_parent_issue_url: str,
    capability_receipt: dict[str, Any],
) -> list[str]:
    """Verify current, deterministic Plan-graph publication preconditions.

    A Plan that has passed its own validation is the authority for publishing its
    derived graph. The account, repository, parent, draft, and capabilities must
    still match before every write.
    """

    errors = validate_graph_draft(draft)
    if errors:
        return errors
    if not isinstance(capability_receipt, dict):
        return ["capability receipt must be an object"]
    if current_repository != draft.get("repository"):
        errors.append("current repository does not match frozen draft")
    if current_parent_issue_url != draft.get("parent_issue_url"):
        errors.append("current parent issue does not match frozen draft")
    expected_capability = {
        "github_login": current_login,
        "github_account_id": current_account_id,
        "repository": current_repository,
        "parent_issue_url": current_parent_issue_url,
        "native_parent_supported": True,
        "blocking_supported": True,
        "readback_supported": True,
    }
    for field, expected in expected_capability.items():
        if capability_receipt.get(field) != expected:
            errors.append(f"capability receipt {field} is missing or stale")
    return errors


def reconcile_graph_state(
    draft: dict[str, Any], remote_state: dict[str, Any]
) -> dict[str, Any]:
    draft_errors = validate_graph_draft(draft)
    if draft_errors:
        raise PlanProtocolError("invalid graph draft: " + "; ".join(draft_errors))
    if not isinstance(remote_state, dict):
        return {"classification": "CONFLICT", "reasons": ["remote readback is not an object"]}
    remote_children = remote_state.get("children")
    remote_edges = remote_state.get("edges")
    if not isinstance(remote_children, list) or not isinstance(remote_edges, list):
        return {"classification": "CONFLICT", "reasons": ["remote readback is incomplete"]}
    expected_children = {child["task_id"]: child for child in draft["children"]}
    observed_ids: set[str] = set()
    reasons: list[str] = []
    for child in remote_children:
        if not isinstance(child, dict):
            reasons.append("remote child is malformed")
            continue
        task_id = child.get("task_id")
        if not isinstance(task_id, str):
            reasons.append("remote child has malformed task ID")
            continue
        if task_id in observed_ids:
            reasons.append(f"duplicate remote stable marker for {task_id}")
            continue
        observed_ids.add(task_id)
        expected = expected_children.get(task_id)
        if expected is None:
            reasons.append(f"extra remote child {task_id}")
            continue
        for field in ("stable_marker", "title", "body_sha256"):
            if child.get(field) != expected[field]:
                reasons.append(f"remote child {task_id} {field} conflicts")
        if child.get("parent_issue_url") != draft["parent_issue_url"]:
            reasons.append(f"remote child {task_id} parent conflicts")
    expected_child_order = [child["task_id"] for child in draft["children"]]
    observed_order = [
        child.get("task_id")
        for child in remote_children
        if isinstance(child, dict) and child.get("task_id") in expected_children
    ]
    child_positions = [expected_child_order.index(task_id) for task_id in observed_order]
    if child_positions != sorted(child_positions):
        reasons.append("remote children are reordered")
    expected_edges = [(edge["blocked"], edge["blocked_by"]) for edge in draft["edges"]]
    observed_edges: list[tuple[Any, Any]] = []
    for edge in remote_edges:
        if not isinstance(edge, dict):
            reasons.append("remote edge is malformed")
            continue
        blocked, blocked_by = edge.get("blocked"), edge.get("blocked_by")
        if not isinstance(blocked, str) or not isinstance(blocked_by, str):
            reasons.append("remote edge has malformed task ID")
            continue
        observed_edges.append((blocked, blocked_by))
    if len(observed_edges) != len(set(observed_edges)):
        reasons.append("duplicate remote dependency edge")
    if any(edge not in expected_edges for edge in observed_edges):
        reasons.append("remote dependency graph contains an extra or conflicting edge")
    # GitHub presents relationship connections in API-defined order (commonly
    # newest-first), not transaction order. Edge membership is authoritative;
    # presentation order is not and must not turn a valid subset into conflict.
    if reasons:
        return {"classification": "CONFLICT", "reasons": sorted(set(reasons))}
    missing_children = [
        child["task_id"] for child in draft["children"] if child["task_id"] not in observed_ids
    ]
    missing_edges = [
        {"blocked": edge[0], "blocked_by": edge[1]}
        for edge in expected_edges
        if edge not in observed_edges
    ]
    if not missing_children and not missing_edges:
        return {"classification": "EXACT_MATCH", "missing_children": [], "missing_edges": []}
    return {
        "classification": "AUTHORIZED_MISSING",
        "missing_children": missing_children,
        "missing_edges": missing_edges,
    }


def verify_final_graph(
    draft: dict[str, Any],
    remote_state: dict[str, Any],
    action_records: Any,
) -> list[str]:
    reconciliation = reconcile_graph_state(draft, remote_state)
    if reconciliation["classification"] != "EXACT_MATCH":
        return [f"remote graph is not exact: {reconciliation}"]
    if not isinstance(action_records, list):
        return ["graph action records must be an array"]
    errors: list[str] = []
    verified: set[tuple[str, str]] = set()
    attempted: set[tuple[str, str]] = set()
    for index, action in enumerate(action_records):
        if not isinstance(action, dict):
            errors.append(f"graph action {index} must be an object")
            continue
        status = action.get("status")
        if status not in ("attempted", "verified", "blocked"):
            errors.append(f"graph action {index} has invalid status")
            continue
        raw_kind = action.get("kind")
        kind = {"create_child": "child", "add_blocked_by": "edge"}.get(
            raw_kind, raw_kind
        )
        key = (kind, action.get("key", action.get("target")))
        if status == "attempted":
            attempted.add(key)
        elif status == "verified":
            if key not in attempted:
                errors.append(f"graph action {index} was verified before attempted")
            verified.add(key)
    expected = {
        ("child", child["task_id"]) for child in draft["children"]
    } | {
        ("edge", f"{edge['blocked']}<-{edge['blocked_by']}") for edge in draft["edges"]
    }
    missing = expected - verified
    if missing:
        errors.append(f"graph actions lack verified records: {sorted(missing)}")
    return errors
