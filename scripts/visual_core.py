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
    r"implementation|validation|recovery|safety|portability|provenance|"
    r"licensing|packaging|verification|orchestration)\b\s*$"
)
NONVISUAL_OBJECT_PHRASE = re.compile(
    r"^\s*(?:validation|validator|test|command|api)\s+"
    r"(?:results?|outputs?|responses?)\s*$"
)
NONVISUAL_OBJECT_PHRASE_VERBS = frozenset(
    {
        "check",
        "compare",
        "emit",
        "inspect",
        "parse",
        "record",
        "return",
        "serialize",
        "store",
        "validate",
        "verify",
    }
)
NONVISUAL_EARS_PREDICATES = (
    r"emit deterministic structural findings tied to the candidate content hash",
    r"prevent final plan validation until a targeted patch receives a fresh independent recheck",
    r"require either a verified patch or an explicit owner, rationale, and disposition",
    r"authenticate a fresh auditor session distinct from all authoring, contestant, judge, phase-auditor, transition-judge, and predecessor-auditor sessions",
    r"verify its role, exact remote-body hash, result hash, callback equality, and remediation lineage from durable trace evidence",
    r"canonicalize its body under the enumerated transport-only rules",
    r"require its hash to equal the body hash reviewed by the final independent plan auditor",
    r"invalidate the audit",
    r"require a fresh independent recheck",
    r"parse stable task ids, owner lanes, and explicit dependency ids without inferring dependencies from prose",
    r"emit a recomputed no_graph receipt under graph-policy/v1",
    r"classify the plan as graph_required",
    r"prohibit child-issue and relationship mutations",
    r"use the implementation issue as parent and preserve each task's complete structured context in one child issue",
    r"reuse exact matches",
    r"resume only the authorized missing items",
    r"stop without creating, editing, or deleting remote artifacts",
    r"require a newly frozen draft plus explicit reauthorization",
    r"remotely verify every child identity, parent link, dependency relationship, and recorded action before issuing final plan valid",
    r"continue to report the plan as not valid",
    r"include the bundled plan-auditor and plan-to-graph skills",
    r"verify their packaged copies remain synchronized",
    r"cross-check the parent delegation event, child uuid session, role marker, callback equality, timestamps, and result hash",
    r"exclude secrets, private prompts, pii, and unsupported performance or quality claims",
    r"enter blocked",
    r"resume only after remote-state reconciliation without destructive cleanup",
    r"record immutable plan_protocol_version: plan-protocol/v2",
    r"retain its recorded contract unless an explicit event-preserving migration is performed",
    r"fail closed",
    r"recompute visual-applicability/v1 from the scoped deliverable, affected surfaces, paths, components, acceptance criteria, and explicit user direction before any screenshot or imagegen call",
    r"normalize their authority, scope, and source order before evaluating any visual trigger",
    r"use the newer direction unless it would waive acceptance-critical evidence",
    r"enter blocked_pending_visual_clarification before any screenshot or imagegen call",
    r"classify the run as visual_required",
    r"select runtime_capture",
    r"not require imagegen",
    r"select generative_mockup",
    r"classify the run as visual_not_applicable",
    r"not use that unrelated frontend as evidence that a visual is required",
    r"default to visual_not_applicable",
    r"block rather than emit visual_not_applicable",
    r"enter blocked_pending_visual_clarification",
    r"not treat the direction as a waiver",
    r"block for clarification",
    r"record the nonmaterial uncertainty",
    r"use the otherwise proven disposition",
    r"require empty contestant-image and final-image receipts",
    r"omit mockup publication and visual-review gates",
    r"verify complete positive nonvisual coverage in the disposition matrix",
    r"require current runtime, dom/accessibility, or visual-regression evidence sufficient for the criteria",
    r"omit imagegen-specific gates",
    r"preserve all screenshot grounding, semantic review, durable publication, mockup accounting, accessibility, and implementation visual-comparison gates",
    r"recompute visual applicability from the approved tasks and intended changed paths",
    r"reject a stale plan receipt",
    r"recompute visual applicability from the actual diff, changed paths, runtime surfaces, and acceptance criteria",
    r"upgrade and block on any newly detected visual trigger",
    r"record the copy-versus-reimplementation decision",
    r"preserve every applicable bsd-2-clause notice or attribution",
)


def _system_predicate_clauses(text: str) -> list[str]:
    subject = re.search(r"\b(?:system|validator)\s+shall\s+", text)
    if subject is None:
        return []
    predicate = re.sub(r"\s*<!--.*$", "", text[subject.end():]).strip().rstrip(".")
    predicate = re.sub(
        r";\s*(?:if|otherwise)\b.*?\b(?:the\s+)?(?:system|validator)\s+shall\s+",
        " and SHALL ",
        predicate,
    )
    clauses = re.split(
        r"(?:,\s*|\s+and\s+)shall\s+",
        predicate,
        flags=re.IGNORECASE,
    )
    return [
        re.sub(r"[`*]", "", clause).strip().strip(",").lower()
        for clause in clauses
        if clause.strip()
    ]


def _is_complete_nonvisual_ears_statement(text: str) -> bool:
    clauses = _system_predicate_clauses(text)
    return bool(clauses) and all(
        any(re.fullmatch(pattern, clause) for pattern in NONVISUAL_EARS_PREDICATES)
        for clause in clauses
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
        r"visual mockups?|mockups?|mood boards?|visual reports?|"
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
        r"\b(aria|focus behavior|css regression|ui copy|screenshots?|existing component|"
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
    if _is_complete_nonvisual_ears_statement(lowered):
        return "nonvisual"
    if any(
        re.search(pattern, lowered)
        for pattern in (
            r"\bimplement\s+visual-applicability/v1\b",
            r"\bimplement\s+stable task parsing and graph-policy/v1\b",
            r"\bbundle\s+collision-safe plan-to-graph\b",
        )
    ):
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
        r"(?:^\s*(?P<leading_verb>create|design|generate|produce|render|draw|illustrate|"
        r"photograph|build|make|craft|compose|fashion|forge|fabricate|"
        r"construct|prepare|provide|deliver|publish|emit)|"
        r"\bshall\s+(?:(?:not|remotely)\s+)?(?P<ears_verb>[a-z][a-z-]*))"
        r"\s+(?:(?:an?|the|new|requested)\s+)?"
        r"(?P<object>.*?)(?=\s+\b(?:for|to|using|with|in|on|while|that|which|whose)\b|[.;:]|$)",
        intent_clause,
    )
    if creation:
        action = creation.group("leading_verb") or creation.group("ears_verb")
        created_object = creation.group("object").strip()
        if (
            action == "emit"
            and re.search(
                r"\b(?:findings?|receipts?|events?|records?|validation results?|"
                r"test outputs?|command outputs?|api responses?)\b",
                created_object,
            )
            and not re.search(r"\b(?:visual|image|badge|mockup|screen)\b", created_object)
        ):
            return "nonvisual"
        if not (
            NONVISUAL_OBJECT_HEAD.search(created_object)
            or (
                NONVISUAL_OBJECT_PHRASE.fullmatch(created_object)
                and action in NONVISUAL_OBJECT_PHRASE_VERBS
            )
        ):
            return "ambiguous"
    leading_action = re.match(
        r"^\s*(?P<action>[a-z][a-z-]*)\s+(?:(?:an?|the)\s+)?"
        r"(?P<object>.*?)(?=\s+\b(?:for|to|using|with|in|on|while|that|which|whose)\b|[.;:]|$)",
        intent_clause,
    )
    if leading_action:
        action = leading_action.group("action")
        object_phrase = leading_action.group("object").strip()
        if not (
            NONVISUAL_OBJECT_HEAD.search(object_phrase)
            or (
                NONVISUAL_OBJECT_PHRASE.fullmatch(object_phrase)
                and action in NONVISUAL_OBJECT_PHRASE_VERBS
            )
        ):
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
