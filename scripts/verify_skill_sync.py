#!/usr/bin/env python3
"""Verify that an installed Evidence-Gated Delivery skill matches this release."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


EXCLUDED = {"__pycache__", ".git"}
INCLUDED_TOP_LEVEL = {"SKILL.md", "agents", "references", "scripts"}


def files(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
        and path.relative_to(root).parts[0] in INCLUDED_TOP_LEVEL
        and not any(part in EXCLUDED for part in path.parts)
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installed-skill", required=True, type=Path)
    args = parser.parse_args()
    release = Path(__file__).resolve().parents[1]
    expected, actual = files(release), files(args.installed_skill.expanduser().resolve())
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(path for path in set(expected) & set(actual) if expected[path] != actual[path])
    if missing or extra or changed:
        print({"status": "DRIFT", "missing": missing, "extra": extra, "changed": changed})
        return 1
    print({"status": "IN_SYNC", "files": len(expected)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
