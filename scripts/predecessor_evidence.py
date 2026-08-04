#!/usr/bin/env python3
"""Authenticate evidence imported from an earlier, phase-isolated run.

The loader is deliberately read-only.  It treats the predecessor receipt and
manifest as immutable content-addressed artifacts, preserves the predecessor's
original parent-thread binding, and returns errors instead of partially trusted
data whenever a binding cannot be proved.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Collection, Mapping
from urllib.parse import urlparse


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PREDECESSOR_BY_PHASE = {"plan": "research", "implement": "plan", "review": "implement"}

EVIDENCE_KEYS = frozenset(
    {
        "phase",
        "receipt_path",
        "receipt_sha256",
        "manifest_path",
        "manifest_sha256",
        "predecessor_parent_thread_id",
        "workflow_version",
        "plan_protocol_version",
        "validated_at",
        "authority",
    }
)
AUTHORITY_KEYS = frozenset(
    {
        "repository",
        "initiative_slug",
        "research_issue_url",
        "implementation_issue_url",
        "implementation_issue_body_sha256",
    }
)

# These fields carry phase work product rather than stable workstream identity.
# Equality with the loaded predecessor is therefore evidence of manual copying,
# not an authenticated import.  Empty initializer defaults are intentionally
# allowed, and current-phase fields are not checked until they become prior work.
RESEARCH_EVIDENCE_FIELDS = frozenset({"research_evidence"})
PLAN_EVIDENCE_FIELDS = frozenset(
    {
        "contestants",
        "judges",
        "contestant_images",
        "judge_rubric",
        "semantic_visual_reviews",
        "plan_audits",
        "plan_events",
        "graph_policy_receipt",
        "graph_capability_receipt",
        "graph_draft",
        "graph_actions",
        "graph_remote_state",
        "phase_receipt_bindings",
        "phase_retrospectives",
        "phase_transition_judgments",
        "automation_decisions",
    }
)
IMPLEMENT_EVIDENCE_FIELDS = frozenset(
    {
        "implementation_workers",
        "test_reviewer",
        "acceptance_reviewer",
        "quality_gates",
    }
)
SUCCESSOR_TRANSITION_FIELDS = frozenset(
    {"phase_retrospectives", "phase_transition_judgments", "automation_decisions"}
)
COPIED_FIELDS_BY_PHASE = {
    "research": RESEARCH_EVIDENCE_FIELDS,
    "plan": RESEARCH_EVIDENCE_FIELDS | PLAN_EVIDENCE_FIELDS,
    "implement": RESEARCH_EVIDENCE_FIELDS | PLAN_EVIDENCE_FIELDS | IMPLEMENT_EVIDENCE_FIELDS,
}

LiveAuthoritySha256 = Callable[[str], str]


@dataclass(frozen=True)
class AuthorityBinding:
    """Stable repository and workstream authority authenticated by the import."""

    repository: str
    initiative_slug: str
    research_issue_url: str
    implementation_issue_url: str
    implementation_issue_body_sha256: str


@dataclass(frozen=True)
class PredecessorBinding:
    """Immutable paths, digests, identity, and original provenance for one phase."""

    phase: str
    status: str
    receipt_path: Path
    receipt_sha256: str
    manifest_path: Path
    manifest_sha256: str
    predecessor_parent_thread_id: str
    workflow_version: str
    plan_protocol_version: str
    validated_at: str
    authority: AuthorityBinding


@dataclass(frozen=True)
class PredecessorEvidence:
    """A fully authenticated predecessor and any authenticated ancestor chain."""

    binding: PredecessorBinding
    manifest: Mapping[str, Any]
    receipt: Mapping[str, Any]
    ancestors: tuple[PredecessorBinding, ...] = ()


@dataclass(frozen=True)
class PredecessorEvidenceResult:
    """Fail-closed result returned to validator integration code."""

    evidence: PredecessorEvidence | None
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return self.evidence is not None and not self.errors

    @property
    def manifest(self) -> Mapping[str, Any] | None:
        return self.evidence.manifest if self.evidence is not None else None

    @property
    def receipt(self) -> Mapping[str, Any] | None:
        return self.evidence.receipt if self.evidence is not None else None

    @property
    def binding(self) -> Mapping[str, Any] | None:
        """Return gate-friendly normalized fields without exposing a mutable dict."""
        if self.evidence is None:
            return None
        normalized = asdict(self.evidence.binding)
        normalized["receipt_path"] = str(self.evidence.binding.receipt_path)
        normalized["manifest_path"] = str(self.evidence.binding.manifest_path)
        return MappingProxyType(normalized)

    @property
    def typed_binding(self) -> PredecessorBinding | None:
        return self.evidence.binding if self.evidence is not None else None


class _DuplicateKeyError(ValueError):
    pass


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _read_json_object(
    path: Path, label: str, errors: list[str]
) -> tuple[dict[str, Any] | None, str]:
    try:
        payload = path.read_bytes()
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite number {constant}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"predecessor {label} must be readable canonical JSON: {exc}")
        return None, ""
    if not isinstance(value, dict):
        errors.append(f"predecessor {label} must contain a JSON object")
        return None, ""
    return value, hashlib.sha256(payload).hexdigest()


def _absolute_file(value: Any, field: str, errors: list[str]) -> Path | None:
    if not isinstance(value, str) or not value:
        errors.append(f"predecessor_evidence.{field} is required")
        return None
    path = Path(value)
    if not path.is_absolute():
        errors.append(f"predecessor_evidence.{field} must be an absolute path")
        return None
    if not path.is_file():
        errors.append(f"predecessor_evidence.{field} must name an existing file")
        return None
    return path


def _digest(value: Any, field: str, errors: list[str]) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        errors.append(f"predecessor_evidence.{field} must be a lowercase SHA-256 digest")
        return ""
    return value


def _repository_from_issue_url(value: str) -> str:
    parsed = urlparse(value)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or parsed.query
        or parsed.fragment
        or len(segments) != 4
    ):
        return ""
    if segments[2] != "issues" or not segments[3].isdigit():
        return ""
    return f"{segments[0]}/{segments[1]}"


def _authority_binding(value: Any, errors: list[str]) -> AuthorityBinding | None:
    if not isinstance(value, dict):
        errors.append("predecessor_evidence.authority must be an object")
        return None
    if set(value) != AUTHORITY_KEYS:
        errors.append("predecessor_evidence.authority must contain exactly the frozen authority fields")
        return None
    strings = {key: value.get(key) for key in AUTHORITY_KEYS}
    if any(not isinstance(item, str) or not item for item in strings.values()):
        errors.append("predecessor_evidence.authority fields must be non-empty strings")
        return None
    body_sha = strings["implementation_issue_body_sha256"]
    if SHA256_RE.fullmatch(body_sha) is None:
        errors.append(
            "predecessor_evidence.authority.implementation_issue_body_sha256 "
            "must be a lowercase SHA-256 digest"
        )
        return None
    repositories = {
        _repository_from_issue_url(strings["research_issue_url"]),
        _repository_from_issue_url(strings["implementation_issue_url"]),
    }
    if "" in repositories or repositories != {strings["repository"]}:
        errors.append("predecessor_evidence.authority issue URLs must match authority.repository")
        return None
    return AuthorityBinding(**strings)


def _manifest_body_sha256(manifest: Mapping[str, Any]) -> str:
    disposition = manifest.get("visual_artifact_disposition")
    if not isinstance(disposition, dict):
        return ""
    binding = disposition.get("phase_binding")
    if not isinstance(binding, dict):
        return ""
    value = binding.get("authoritative_issue_body_sha256")
    return value if isinstance(value, str) else ""


def _manifest_authority_errors(
    manifest: Mapping[str, Any], authority: AuthorityBinding, label: str
) -> list[str]:
    errors: list[str] = []
    identity = manifest.get("initiative_identity")
    if not isinstance(identity, dict):
        return [f"{label}.initiative_identity must be an object"]
    expected = {
        "slug": authority.initiative_slug,
        "research_issue_url": authority.research_issue_url,
        "implementation_issue_url": authority.implementation_issue_url,
    }
    for field, value in expected.items():
        if identity.get(field) != value:
            errors.append(f"{label}.initiative_identity.{field} does not match predecessor authority")
    if manifest.get("research_issue_url") != authority.research_issue_url:
        errors.append(f"{label}.research_issue_url does not match predecessor authority")
    if manifest.get("implementation_issue_url") != authority.implementation_issue_url:
        errors.append(f"{label}.implementation_issue_url does not match predecessor authority")
    if _manifest_body_sha256(manifest) != authority.implementation_issue_body_sha256:
        errors.append(f"{label} authoritative implementation issue body SHA-256 does not match")
    return errors


def _meaningful(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_meaningful(item) for item in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_meaningful(item) for item in value)
    return value not in (None, "", False, 0)


def _copied_evidence_errors(
    successor: Mapping[str, Any], predecessor: Mapping[str, Any], phase: str
) -> list[str]:
    errors: list[str] = []
    for field in sorted(COPIED_FIELDS_BY_PHASE.get(phase, ())):
        if field in SUCCESSOR_TRANSITION_FIELDS:
            entries = successor.get(field)
            if not isinstance(entries, list):
                continue
            phase_field = "from_phase" if field == "automation_decisions" else "phase"
            if any(
                isinstance(entry, dict) and entry.get(phase_field) != phase
                for entry in entries
            ):
                errors.append(
                    f"successor.{field} contains earlier-phase evidence; use authenticated import"
                )
            continue
        if field == "plan_events":
            # Every v2 run has its own activation event. Exact equality still
            # detects copying while preserving the successor's authenticated event.
            prior_value = predecessor.get(field)
            if _meaningful(prior_value) and successor.get(field) == prior_value:
                errors.append(
                    f"successor.{field} duplicates predecessor-only evidence; use authenticated import"
                )
            continue
        if _meaningful(successor.get(field)):
            errors.append(
                f"successor.{field} contains predecessor-only evidence; use authenticated import"
            )
            continue
        prior_value = predecessor.get(field)
        if _meaningful(prior_value) and successor.get(field) == prior_value:
            errors.append(
                f"successor.{field} duplicates predecessor-only evidence; use authenticated import"
            )
    return errors


def validate_predecessor_evidence(
    successor: Mapping[str, Any],
    expected_phase: str,
    *,
    live_authority_sha256: LiveAuthoritySha256,
    ancestor_receipt_sha256s: Collection[str] = (),
    trusted_receipt_sha256s: Collection[str] = (),
) -> PredecessorEvidenceResult:
    """Load and authenticate ``successor['predecessor_evidence']``.

    ``live_authority_sha256`` receives the implementation issue URL and must
    return the SHA-256 of its current remote body.  Callers own network access;
    this module performs no writes and no implicit network operations.
    ``ancestor_receipt_sha256s`` lets recursive integrations reject a receipt
    already present in the active import chain.
    """

    errors: list[str] = []
    if expected_phase not in COPIED_FIELDS_BY_PHASE:
        return PredecessorEvidenceResult(
            None, (f"unsupported predecessor phase {expected_phase!r}",)
        )
    raw = successor.get("predecessor_evidence")
    if not isinstance(raw, dict):
        return PredecessorEvidenceResult(
            None, ("predecessor_evidence must be an object",)
        )
    if set(raw) != EVIDENCE_KEYS:
        errors.append("predecessor_evidence must contain exactly the frozen binding fields")
    if raw.get("phase") != expected_phase:
        errors.append(f"predecessor_evidence.phase must equal {expected_phase}")

    receipt_sha = _digest(raw.get("receipt_sha256"), "receipt_sha256", errors)
    manifest_sha = _digest(raw.get("manifest_sha256"), "manifest_sha256", errors)
    if receipt_sha and receipt_sha in set(ancestor_receipt_sha256s):
        errors.append("predecessor evidence cycle detected from repeated receipt SHA-256")
    if receipt_sha and receipt_sha not in set(trusted_receipt_sha256s):
        errors.append(
            "predecessor receipt SHA-256 lacks an authenticated transition anchor"
        )

    receipt_path = _absolute_file(raw.get("receipt_path"), "receipt_path", errors)
    manifest_path = _absolute_file(raw.get("manifest_path"), "manifest_path", errors)
    authority = _authority_binding(raw.get("authority"), errors)
    if receipt_path is None or manifest_path is None or authority is None:
        return PredecessorEvidenceResult(None, tuple(errors))

    receipt, actual_receipt_sha = _read_json_object(receipt_path, "receipt", errors)
    manifest, actual_manifest_sha = _read_json_object(manifest_path, "manifest", errors)
    if receipt is None or manifest is None:
        return PredecessorEvidenceResult(None, tuple(errors))
    if receipt_sha and actual_receipt_sha != receipt_sha:
        errors.append("predecessor receipt SHA-256 does not match exact receipt bytes")
    if manifest_sha and actual_manifest_sha != manifest_sha:
        errors.append("predecessor manifest SHA-256 does not match exact manifest bytes")

    if receipt.get("status") != "VALID":
        errors.append("predecessor receipt status must be VALID")
    if receipt.get("errors") != []:
        errors.append("predecessor receipt errors must be an empty array")
    if receipt.get("remote_verification") is not True:
        errors.append("predecessor receipt must record remote_verification true")
    if receipt.get("phase") != expected_phase:
        errors.append(f"predecessor receipt phase must equal {expected_phase}")
    # Older VALID receipts predate the optional receipt_path self-field.  The
    # successor's absolute path plus digest is authoritative; if a receipt does
    # carry the optional self-field, it still may not contradict that binding.
    if "receipt_path" in receipt and receipt.get("receipt_path") != str(receipt_path):
        errors.append("predecessor receipt_path contradicts the exact successor binding")
    if receipt.get("manifest") != str(manifest_path):
        errors.append("predecessor manifest_path does not match the receipt manifest binding")
    if receipt.get("manifest_sha256") != manifest_sha:
        errors.append("predecessor manifest_sha256 does not match the receipt manifest binding")
    if receipt.get("run_id") != manifest.get("run_id"):
        errors.append("predecessor receipt run_id does not match its manifest")

    string_bindings = (
        ("predecessor_parent_thread_id", manifest.get("parent_thread_id")),
        ("workflow_version", manifest.get("workflow_version")),
        ("plan_protocol_version", manifest.get("plan_protocol_version")),
        ("validated_at", receipt.get("validated_at")),
    )
    for field, expected in string_bindings:
        if not isinstance(raw.get(field), str) or not raw.get(field):
            errors.append(f"predecessor_evidence.{field} must be a non-empty string")
        elif raw.get(field) != expected:
            errors.append(f"predecessor_evidence.{field} does not match original predecessor provenance")
    if raw.get("workflow_version") != successor.get("workflow_version"):
        errors.append("predecessor workflow_version does not match successor")
    if raw.get("plan_protocol_version") != successor.get("plan_protocol_version"):
        errors.append("predecessor plan_protocol_version does not match successor")

    errors.extend(_manifest_authority_errors(manifest, authority, "predecessor"))
    errors.extend(_manifest_authority_errors(successor, authority, "successor"))
    errors.extend(_copied_evidence_errors(successor, manifest, expected_phase))

    try:
        live_sha = live_authority_sha256(authority.implementation_issue_url)
    except Exception as exc:  # dependency boundary: fail closed on any reader failure
        errors.append(f"live predecessor authority verification failed: {exc}")
    else:
        if live_sha != authority.implementation_issue_body_sha256:
            errors.append("live predecessor authority body SHA-256 is stale or mismatched")

    ancestors: tuple[PredecessorBinding, ...] = ()
    nested = manifest.get("predecessor_evidence")
    if nested not in (None, {}):
        nested_expected = PREDECESSOR_BY_PHASE.get(expected_phase)
        if nested_expected is None:
            errors.append(f"{expected_phase} predecessor cannot import an earlier phase")
        elif not isinstance(nested, dict):
            errors.append("predecessor manifest predecessor_evidence must be an object")
        else:
            nested_result = validate_predecessor_evidence(
                manifest,
                nested_expected,
                live_authority_sha256=live_authority_sha256,
                ancestor_receipt_sha256s=(*ancestor_receipt_sha256s, receipt_sha),
                trusted_receipt_sha256s={str(nested.get("receipt_sha256", ""))},
            )
            errors.extend(f"ancestor: {error}" for error in nested_result.errors)
            if nested_result.evidence is not None:
                ancestors = (
                    nested_result.evidence.binding,
                    *nested_result.evidence.ancestors,
                )

    if errors:
        return PredecessorEvidenceResult(None, tuple(errors))

    binding = PredecessorBinding(
        phase=expected_phase,
        status="VALID",
        receipt_path=receipt_path,
        receipt_sha256=receipt_sha,
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha,
        predecessor_parent_thread_id=raw["predecessor_parent_thread_id"],
        workflow_version=raw["workflow_version"],
        plan_protocol_version=raw["plan_protocol_version"],
        validated_at=raw["validated_at"],
        authority=authority,
    )
    # Mapping proxies prevent accidental in-process mutation of authenticated
    # dictionaries.  Nested values remain read-only by convention; their bytes
    # are still protected by the returned content digests.
    evidence = PredecessorEvidence(
        binding=binding,
        manifest=MappingProxyType(manifest),
        receipt=MappingProxyType(receipt),
        ancestors=ancestors,
    )
    return PredecessorEvidenceResult(evidence, ())
