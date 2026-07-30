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
import struct
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any

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
        r"technical diagram|emoji pack)\b",
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


def _valid_png(data: bytes) -> bool:
    """Strictly parse a non-interlaced PNG and its decompressed scanlines."""

    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset = 8
    width = height = bit_depth = color_type = None
    compressed = bytearray()
    saw_iend = False
    saw_ihdr = False
    saw_idat = False
    idat_ended = False
    saw_plte = False
    palette_entries = 0
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        if (
            len(chunk_type) != 4
            or not all(
                ord("A") <= byte <= ord("Z")
                or ord("a") <= byte <= ord("z")
                for byte in chunk_type
            )
            or chunk_type[2] & 0x20
        ):
            return False
        end = offset + 12 + length
        if end > len(data):
            return False
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != expected_crc:
            return False
        if chunk_type == b"IHDR":
            if offset != 8 or length != 13 or saw_ihdr:
                return False
            saw_ihdr = True
            (
                width,
                height,
                bit_depth,
                color_type,
                compression,
                filtering,
                interlace,
            ) = struct.unpack(">IIBBBBB", payload)
            if (
                width < 1
                or height < 1
                or compression != 0
                or filtering != 0
                or interlace != 0
            ):
                return False
        elif chunk_type == b"IDAT":
            if not saw_ihdr or idat_ended:
                return False
            saw_idat = True
            if len(compressed) + len(payload) > 50 * 1024 * 1024:
                return False
            compressed.extend(payload)
        elif chunk_type == b"IEND":
            if length != 0 or end != len(data) or not saw_idat:
                return False
            saw_iend = True
            break
        elif chunk_type == b"PLTE":
            if (
                saw_plte
                or saw_idat
                or color_type in {0, 4}
                or length < 3
                or length > 768
                or length % 3
            ):
                return False
            saw_plte = True
            palette_entries = length // 3
        elif chunk_type and chunk_type[0] & 0x20 == 0:
            return False
        elif saw_idat:
            idat_ended = True
        offset = end
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    allowed_depths = {
        0: {1, 2, 4, 8, 16},
        2: {8, 16},
        3: {1, 2, 4, 8},
        4: {8, 16},
        6: {8, 16},
    }
    if (
        not saw_iend
        or channels is None
        or bit_depth not in allowed_depths.get(color_type, set())
        or not compressed
        or (color_type == 3 and not saw_plte)
        or (
            color_type == 3
            and palette_entries > 2 ** int(bit_depth)
        )
    ):
        return False
    row_bytes = (int(width) * channels * int(bit_depth) + 7) // 8
    expected_size = int(height) * (row_bytes + 1)
    if expected_size > 100 * 1024 * 1024:
        return False
    try:
        decompressor = zlib.decompressobj()
        scanlines = decompressor.decompress(
            bytes(compressed), expected_size + 1
        )
    except zlib.error:
        return False
    if (
        len(scanlines) != expected_size
        or decompressor.unconsumed_tail
        or decompressor.unused_data
        or not decompressor.eof
    ):
        return False
    bytes_per_pixel = max(1, (channels * int(bit_depth) + 7) // 8)
    previous = bytearray(row_bytes)
    reconstructed_rows: list[bytes] = []
    for row in range(int(height)):
        start = row * (row_bytes + 1)
        filter_type = scanlines[start]
        if filter_type not in range(5):
            return False
        raw = scanlines[start + 1 : start + 1 + row_bytes]
        reconstructed = bytearray(row_bytes)
        for index, value in enumerate(raw):
            left = reconstructed[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            above = previous[index]
            upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            else:
                estimate = left + above - upper_left
                distances = (
                    abs(estimate - left),
                    abs(estimate - above),
                    abs(estimate - upper_left),
                )
                predictor = (left, above, upper_left)[distances.index(min(distances))]
            reconstructed[index] = (value + predictor) & 0xFF
        reconstructed_rows.append(bytes(reconstructed))
        previous = reconstructed
    if color_type == 3:
        mask = (1 << int(bit_depth)) - 1
        for row in reconstructed_rows:
            for pixel in range(int(width)):
                bit_offset = pixel * int(bit_depth)
                byte = row[bit_offset // 8]
                shift = 8 - int(bit_depth) - (bit_offset % 8)
                if (byte >> shift) & mask >= palette_entries:
                    return False
    return True


def runtime_evidence_sufficient(
    scope_id: str,
    evidence: Any,
    *,
    not_before: str | None = None,
) -> bool:
    """Prove current, scope-bound runtime evidence for one inventory entry."""

    try:
        threshold = (
            datetime.fromisoformat(not_before.replace("Z", "+00:00"))
            if isinstance(not_before, str) and not_before
            else None
        )
    except ValueError:
        return False
    if threshold is not None and threshold.tzinfo is None:
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
        if kind in {"screenshot", "visual_regression"} and not _valid_png(
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
        if threshold is not None and captured < threshold:
            continue
        return True
    return False


def _intent_group(text: str) -> str:
    lowered = text.lower()
    inferred = _inferred_kind_group({"source": text})
    if inferred in {"generative", "runtime"}:
        return inferred
    intent_clause = re.split(
        r"(?i)\b(?:affected modules|requirements|verification|complete when):",
        lowered,
        maxsplit=1,
    )[0]
    creation = re.search(
        r"\b(?:create|design|generate|produce|render|draw|illustrate|photograph)"
        r"\s+(?:(?:an?|the|new|requested)\s+)?"
        r"(?P<object>.*?)(?=\s+\b(?:for|to|using|with|in|on|while|that|which|whose)\b|[.;:]|$)",
        intent_clause,
    )
    if creation:
        created_object = creation.group("object").strip()
        if not re.search(
            r"\b(?:validator|verifier|workflow|automation|tests?|fixtures?|"
            r"documentation|readme|api|endpoint|service|functions?|methods?|"
            r"scripts?|tools?|library|cli|backend|migration|schemas?|contracts?|"
            r"parsers?|serializers?|manifests?|receipts?|events?|checks?|gates?|"
            r"polic(?:y|ies)|rules?|configurations?|configs?|integrations?|logic|"
            r"handlers?|behaviors?|states?|support|mechanisms?|capabilities?|"
            r"protocols?|markers?|records?|adapters?|modules?|packages?|"
            r"dependencies|types?|fields?|commands?|jobs?|settings?|templates?|"
            r"prompts?|skills?)\b\s*$",
            created_object,
        ):
            return "ambiguous"
    if re.search(
        r"\b(visual[- ]applicability|evidence[_ -]mode|generative_mockup|"
        r"classif(?:y|ication)|selects? (?:the )?(?:visual|runtime|generative))\b",
        lowered,
    ):
        return "nonvisual"
    return inferred or "nonvisual"


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
