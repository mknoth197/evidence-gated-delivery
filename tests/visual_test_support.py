from __future__ import annotations

import importlib.util
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "visual_applicability", ROOT / "scripts" / "visual_applicability.py"
)
assert SPEC and SPEC.loader
visual = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(visual)


def png_bytes(
    scanlines: bytes = b"\x00\x00\x00\x00\xff",
    *,
    color_type: int = 6,
    extra_chunks: tuple[tuple[bytes, bytes], ...] = (),
) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            chunk(
                b"IHDR",
                struct.pack(">IIBBBBB", 1, 1, 8, color_type, 0, 0, 0),
            ),
            *(chunk(kind, payload) for kind, payload in extra_chunks),
            chunk(b"IDAT", zlib.compress(scanlines)),
            chunk(b"IEND", b""),
        )
    )


def entry(identifier: str, kind: str = "nonvisual", **extra):
    source_by_group = {
        "nonvisual": "validator workflow in scripts/tool.py",
        "runtime": "existing user-visible interface behavior",
        "generative": "new screen visual concept",
    }
    group = (
        "runtime"
        if kind in visual.RUNTIME_KINDS
        else "generative"
        if kind in visual.GENERATIVE_KINDS
        else "nonvisual"
    )
    value = {
        "id": identifier,
        "kind": kind,
        "source": source_by_group[group],
        "provenance": f"evidence for {identifier}",
        **extra,
    }
    if kind in visual.RUNTIME_KINDS and "runtime_evidence_sufficient" not in value:
        value["runtime_evidence_sufficient"] = True
    return value


def base_inventory(kind: str = "nonvisual"):
    scoped_path = (
        "web/NewScreen.tsx"
        if kind in visual.GENERATIVE_KINDS
        else "web/ExistingPanel.tsx"
        if kind in visual.RUNTIME_KINDS
        else "scripts/tool.py"
    )
    return {
        "deliverables": [entry("D-001", kind)],
        "user_directions": [
            entry(
                "UD-001",
                directive="neutral",
                authority="user",
                scope="D-001",
                source_order=1,
                turn="one",
            )
        ],
        "acceptance_criteria": [entry("AC-001", kind)],
        "tasks": [entry("T-001", kind)],
        "affected_modules": [entry("M-001", kind, source=scoped_path)],
        "planned_paths": [entry("P-001", kind, path=scoped_path)],
    }


def declarations(inventory):
    return {
        domain: [item["id"] for item in inventory[domain]]
        for domain in visual.DOMAIN_PREFIXES
        if domain in inventory
    }


def evaluate(inventory, **kwargs):
    return visual.evaluate_visual_applicability(
        inventory,
        phase=kwargs.pop("phase", "plan"),
        authoritative_issue_body=kwargs.pop("body", "authoritative body"),
        declared_ids=kwargs.pop("declared_ids", declarations(inventory)),
        **kwargs,
    )
