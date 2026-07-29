#!/usr/bin/env python3
"""Fail-closed CLI for deterministic Plan candidate linting."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from plan_protocol import lint_plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("issue_body", type=Path, help="Markdown issue body to lint")
    parser.add_argument("--output", type=Path, help="Optional JSON receipt path")
    args = parser.parse_args()
    try:
        body = args.issue_body.read_text(encoding="utf-8")
        receipt = lint_plan(body)
    except (OSError, UnicodeError, ValueError) as exc:
        receipt = {"status": "FAIL", "findings": [{"finding_id": "LINT-000", "severity": "Blocker", "evidence": str(exc)}]}
    rendered = json.dumps(receipt, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if receipt.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
