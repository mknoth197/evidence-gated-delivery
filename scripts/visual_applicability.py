#!/usr/bin/env python3
"""Stable public facade and CLI for visual applicability."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from visual_core import *  # noqa: F401,F403
from visual_inventory import *  # noqa: F401,F403
from visual_policy import *  # noqa: F401,F403

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("body", type=Path)
    parser.add_argument("--phase", default="plan")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--user-direction",
        action="append",
        required=True,
        help="Persisted effective user direction; repeat in source order",
    )
    args = parser.parse_args()
    try:
        body = args.body.read_text(encoding="utf-8")
        inventory, extraction_errors = build_plan_inventory(
            body, user_directions=args.user_direction
        )
        declared = {
            domain: [entry["id"] for entry in entries]
            for domain, entries in inventory.items()
            if domain in DOMAIN_PREFIXES and isinstance(entries, list)
        }
        receipt = evaluate_visual_applicability(
            inventory,
            phase=args.phase,
            authoritative_issue_body=body,
            declared_ids=declared,
        )
        if extraction_errors:
            receipt["blocking_reasons"] = sorted(
                set(receipt["blocking_reasons"] + extraction_errors)
            )
            receipt["status"] = "blocked"
            receipt["decision"] = BLOCKED_DECISION
            receipt["evidence_mode"] = None
        rendered = json.dumps(receipt, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0 if receipt["status"] == "resolved" else 1
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
