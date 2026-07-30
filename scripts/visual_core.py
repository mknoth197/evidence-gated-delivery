#!/usr/bin/env python3
"""Deterministic visual-applicability policy and receipt validation.

The policy consumes explicit, provenance-bearing scope evidence. Repository-wide
signals are never sufficient on their own: a frontend, CSS file, or web
framework only matters when it is represented by an in-scope inventory entry.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from runtime_artifact_validation import valid_png

POLICY_VERSION = "visual-applicability/v1"
BLOCKED_DECISION = "BLOCKED_PENDING_VISUAL_CLARIFICATION"
DECISION_BY_MODE = {
    "none": "VISUAL_NOT_APPLICABLE",
    "runtime_capture": "VISUAL_REQUIRED",
    "generative_mockup": "VISUAL_REQUIRED",
}

DOMAIN_PREFIXES = {
    "deliverables": "D",
    "user_directions": "UD",
    "acceptance_criteria": "AC",
    "tasks": "T",
    "affected_modules": "M",
    "planned_paths": "P",
    "actual_paths": "P",
    "runtime_surfaces": "RS",
}
REQUIRED_DOMAINS = (
    "deliverables",
    "user_directions",
    "acceptance_criteria",
    "tasks",
    "affected_modules",
)
NONVISUAL_KINDS = {
    "backend",
    "cli",
    "library",
    "validator",
    "workflow",
    "automation",
    "ci",
    "infrastructure",
    "migration",
    "data_contract",
    "security",
    "observability",
    "process_documentation",
    "docs_mermaid",
    "nonvisual",
}
RUNTIME_KINDS = {
    "ui_copy",
    "aria",
    "focus_behavior",
    "css_regression",
    "existing_component_state",
    "existing_visual_behavior",
    "frontend_affecting_contract",
    "user_visible_interface",
}
GENERATIVE_KINDS = {
    "new_screen",
    "new_component",
    "new_visual_concept",
    "generated_web_asset",
    "redesign",
    "marketing_asset",
    "inherently_visual",
}
VALID_KINDS = NONVISUAL_KINDS | RUNTIME_KINDS | GENERATIVE_KINDS
AUTHORITY_RANK = {
    "repository": 10,
    "plan": 20,
    "acceptance": 30,
    "user": 40,
    "system": 50,
}
NONVISUAL_OBJECT_HEAD = re.compile(
    r"\b(?:validator|verifier|workflow|automation|tests?|fixtures?|"
    r"documentation|readme|api|endpoint|service|functions?|methods?|"
    r"scripts?|tools?|library|cli|backend|migration|schemas?|contracts?|"
    r"parsers?|serializers?|manifests?|receipts?|events?|checks?|gates?|"
    r"polic(?:y|ies)|rules?|configurations?|configs?|integrations?|logic|"
    r"handlers?|behaviors?|states?|support|mechanisms?|capabilities?|"
    r"protocols?|markers?|records?|adapters?|modules?|packages?|"
    r"dependencies|types?|fields?|commands?|jobs?|settings?|prompts?|"
    r"skills?|reliability|performance|latency|authentication|planning|"
    r"implementation|validation|results?|outputs?)\b\s*$"
)


def canonical_sha256(value: Any) -> str:
    """Hash JSON canonically, or text after transport-only newline normalization."""

    if isinstance(value, str):
        encoded = value.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    else:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def markdown_section(body: str, heading: str) -> str:
    match = re.search(
        rf"(?ims)^##[ \t]+{re.escape(heading)}[ \t]*$\n(.*?)(?=^##[ \t]+|\Z)",
        body,
    )
    return match.group(1).strip() if match else ""


def _sequential(ids: list[str], prefix: str) -> bool:
    return ids == [f"{prefix}-{index:03d}" for index in range(1, len(ids) + 1)]


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _inferred_kind_group(entry: dict[str, Any]) -> str | None:
    text = " ".join(
        str(entry.get(field, ""))
        for field in ("source", "path", "provenance")
    ).lower()
    if re.search(
        r"\b(new[ _.-]?screen|new page|new component|new visual concept|redesign|"
        r"marketing asset|generated (?:web )?asset|landing page|hero image|"
        r"product illustration|illustrations?|icon set|brand asset|social card|"
        r"poster|thumbnail|company logo|logos?|cover artwork|artwork|"
        r"product photograph|photographs?|photos?|photography|graphics?|"
        r"visual asset|brand identity|avatars?|animations?|infographics?|"
        r"technical diagram|emoji pack|html email template|presentation template|"
        r"(?:printed )?certificate template|event invitation template|visual template)\b",
        text,
    ):
        return "generative"
    if re.search(
        r"\b(aria|focus behavior|css regression|ui copy|existing component|"
        r"existing visual|responsive|visual accessibility|user-visible interface)\b",
        text,
    ):
        return "runtime"
    path = str(entry.get("path") or entry.get("source") or "").lower().strip("` ")
    if re.search(r"\.(?:png|jpe?g|gif|webp|svg|ico|avif)$", path):
        return "generative"
    if re.search(r"(?:^|/)(?:web|ui|frontend|mobile|desktop|components?)/", path) or re.search(
        r"\.(?:tsx|jsx|vue|svelte|css|scss|sass|less)$", path
    ):
        return "runtime"
    if re.search(
        r"(?:^|/)(?:scripts?|tests?|references?|bundled-skills?|infra|migrations?|"
        r"\.github/workflows?)/",
        path,
    ) or re.search(r"\.(?:py|sh|bash|json|ya?ml|toml|sql|md)$", path):
        return "nonvisual"
    if re.search(
        r"\b(backend|cli|library|validator|verifier|workflow|automation|ci/cd|"
        r"infrastructure|migration|data contract|security rule|observability|"
        r"process documentation|mermaid|nonvisual|tests?|fixtures?|references?|"
        r"helpers?|readme|contracts?|schemas?|packaging|notices?|provenance|"
        r"serializers?|scripts?)\b",
        text,
    ):
        return "nonvisual"
    return None


def _declared_kind_group(kind: Any) -> str | None:
    if kind in NONVISUAL_KINDS:
        return "nonvisual"
    if kind in RUNTIME_KINDS:
        return "runtime"
    if kind in GENERATIVE_KINDS:
        return "generative"
    return None


def runtime_evidence_sufficient(
    scope_id: str,
    evidence: Any,
    *,
    not_before: str | None = None,
    not_after: str | None = None,
) -> bool:
    """Prove current, scope-bound runtime evidence for one inventory entry."""

    try:
        threshold = (
            datetime.fromisoformat(not_before.replace("Z", "+00:00"))
            if isinstance(not_before, str) and not_before
            else None
        )
        ceiling = (
            datetime.fromisoformat(not_after.replace("Z", "+00:00"))
            if isinstance(not_after, str) and not_after
            else None
        )
    except ValueError:
        return False
    if (
        (threshold is not None and threshold.tzinfo is None)
        or ceiling is None
        or ceiling.tzinfo is None
    ):
        return False
    for item in evidence if isinstance(evidence, list) else []:
        if not isinstance(item, dict):
            continue
        scope_ids = item.get("scope_ids")
        if not isinstance(scope_ids, list) or scope_id not in scope_ids:
            continue
        if item.get("kind") not in {
            "screenshot",
            "dom_accessibility",
            "visual_regression",
            "runtime_recording",
        }:
            continue
        if not isinstance(item.get("evidence"), str) or not item["evidence"].strip():
            continue
        artifact_hash = item.get("artifact_sha256") or item.get("sha256")
        artifact_path = item.get("artifact_path")
        if not re.fullmatch(r"[0-9a-f]{64}", str(artifact_hash)):
            continue
        if not isinstance(artifact_path, str) or not artifact_path.strip():
            continue
        resolved_artifact = Path(artifact_path).expanduser()
        if not resolved_artifact.is_absolute() or not resolved_artifact.is_file():
            continue
        try:
            if resolved_artifact.stat().st_size > 50 * 1024 * 1024:
                continue
            artifact_bytes = resolved_artifact.read_bytes()
        except (OSError, ValueError):
            continue
        if hashlib.sha256(artifact_bytes).hexdigest() != artifact_hash:
            continue
        kind = item.get("kind")
        if kind in {"screenshot", "visual_regression"} and not valid_png(
            artifact_bytes
        ):
            continue
        if kind == "runtime_recording":
            continue
        if kind == "dom_accessibility":
            try:
                parsed_artifact = json.loads(artifact_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(parsed_artifact, (dict, list)):
                continue
        if not artifact_bytes:
            continue
        try:
            captured = datetime.fromisoformat(
                str(item.get("captured_at", "")).replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if captured.tzinfo is None:
            continue
        if captured > ceiling + timedelta(minutes=5):
            continue
        if threshold is not None and captured < threshold:
            continue
        return True
    return False


def _intent_group(text: str) -> str:
    lowered = text.lower()
    if not lowered.strip():
        return "nonvisual"
    inferred = _inferred_kind_group({"source": text})
    if inferred in {"generative", "runtime"}:
        return inferred
    intent_clause = re.split(
        r"(?i)\b(?:affected modules|requirements|verification|complete when):",
        lowered,
        maxsplit=1,
    )[0]
    creation = re.search(
        r"(?:^\s*(?:create|design|generate|produce|render|draw|illustrate|"
        r"photograph|build|make|craft|compose|fashion|forge|fabricate|"
        r"construct|prepare|provide|deliver|publish|emit)|"
        r"\bshall\s+(?:provide|deliver|publish|emit|display|show|create|render|"
        r"export|return))"
        r"\s+(?:(?:an?|the|new|requested)\s+)?"
        r"(?P<object>.*?)(?=\s+\b(?:for|to|using|with|in|on|while|that|which|whose)\b|[.;:]|$)",
        intent_clause,
    )
    if creation:
        created_object = creation.group("object").strip()
        if not NONVISUAL_OBJECT_HEAD.search(created_object):
            return "ambiguous"
    leading_action = re.match(
        r"^\s*[a-z][a-z-]*\s+(?:(?:an?|the)\s+)?"
        r"(?P<object>.*?)(?=\s+\b(?:for|to|using|with|in|on|while|that|which|whose)\b|[.;:]|$)",
        intent_clause,
    )
    if leading_action:
        object_phrase = leading_action.group("object").strip()
        if not NONVISUAL_OBJECT_HEAD.search(object_phrase):
            return "ambiguous"
    if re.search(
        r"\b(visual[- ]applicability|evidence[_ -]mode|generative_mockup|"
        r"classif(?:y|ication)|selects? (?:the )?(?:visual|runtime|generative))\b",
        lowered,
    ):
        return "nonvisual"
    return inferred or "unknown"


def _direction_entry(identifier: str, source: str, order: int) -> dict[str, Any]:
    lowered = source.lower()
    if re.search(
        r"\b(?:no|avoid|skip|without|do not|should not|when not)\b"
        r".{0,80}\b(?:imagegen|images?|mockups?)\b",
        lowered,
    ) or re.search(
        r"\b(?:imagegen|images?|mockups?)\b.{0,80}\b(?:do not|should not)\b",
        lowered,
    ):
        directive = "suppress"
    elif re.search(
        r"\b(?:request|generate|create|use)\b.{0,40}\b(?:imagegen|visual exploration)\b",
        lowered,
    ):
        directive = "request"
    else:
        directive = "neutral"
    return {
        "id": identifier,
        "kind": "nonvisual",
        "source": source,
        "source_sha256": canonical_sha256(source),
        "provenance": "persisted user direction",
        "directive": directive,
        "authority": "user",
        "scope": "D-001",
        "source_order": order,
        "turn": str(order),
    }
