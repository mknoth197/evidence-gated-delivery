#!/usr/bin/env python3
"""Persist post-transition outcomes for confidence calibration."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--history", type=Path, default=Path.home() / ".codex" / "evidence-gated-delivery-history" / "transition-outcomes.jsonl")
    args = parser.parse_args()
    outcome = json.loads(args.input.read_text())
    required = {"run_id", "from_phase", "to_phase", "judge_confidence", "technical_accuracy_score", "ci_passed", "escaped_defect", "substantive_review_findings"}
    missing = sorted(key for key in required if key not in outcome)
    if missing:
        raise ValueError(f"missing outcome fields: {', '.join(missing)}")
    if isinstance(outcome["judge_confidence"], bool) or not isinstance(outcome["judge_confidence"], int) or not 0 <= outcome["judge_confidence"] <= 10:
        raise ValueError("judge_confidence must be an integer from 0 through 10")
    outcome["recorded_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    args.history.parent.mkdir(parents=True, exist_ok=True)
    with args.history.open("a") as stream:
        stream.write(json.dumps(outcome, sort_keys=True) + "\n")
    print(json.dumps(outcome, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
