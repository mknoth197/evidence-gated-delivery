#!/usr/bin/env python3
"""Explicitly migrate a legacy Plan manifest to plan-protocol/v2."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from plan_protocol import PlanProtocolError, migrate_manifest_to_v2


def _atomic_write(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--recorded-at", help="Explicit ISO-8601 event timestamp")
    parser.add_argument("--event-id", help="Explicit UUID for reproducible fixtures")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        migrated = migrate_manifest_to_v2(
            manifest,
            recorded_at=args.recorded_at,
            event_id=args.event_id,
            persist_activation=not args.dry_run,
        )
        rendered = json.dumps(migrated, indent=2) + "\n"
        if not args.dry_run:
            _atomic_write(args.manifest, rendered)
        print(rendered, end="")
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, PlanProtocolError) as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
