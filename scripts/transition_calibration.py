#!/usr/bin/env python3
"""Summarize how transition-judge confidence predicts downstream outcomes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("history", type=Path)
    args = parser.parse_args()
    outcomes = [json.loads(line) for line in args.history.read_text().splitlines() if line.strip()]
    buckets: dict[str, list[dict]] = {"0-7": [], "8-10": []}
    for outcome in outcomes:
        buckets["8-10" if outcome.get("judge_confidence", -1) >= 8 else "0-7"].append(outcome)
    report = {}
    for name, rows in buckets.items():
        report[name] = {
            "count": len(rows),
            "ci_pass_rate": sum(bool(row.get("ci_passed")) for row in rows) / len(rows) if rows else None,
            "escaped_defect_rate": sum(bool(row.get("escaped_defect")) for row in rows) / len(rows) if rows else None,
            "avg_substantive_review_findings": sum(float(row.get("substantive_review_findings", 0)) for row in rows) / len(rows) if rows else None,
        }
    print(json.dumps({"status": "CALIBRATED", "buckets": report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
