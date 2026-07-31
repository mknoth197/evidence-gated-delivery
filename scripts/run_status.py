#!/usr/bin/env python3
"""Render the current run state and transition blockers as machine-readable JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    data = json.loads(args.manifest.read_text())
    policy = data.get("automation_policy", {})
    timeline = data.get("phase_timeline", {})
    completed = [phase for phase in ("research", "plan", "implement", "review") if timeline.get(f"{phase}_completed_at")]
    judgments = data.get("phase_transition_judgments", [])
    latest = judgments[-1] if isinstance(judgments, list) and judgments else None
    print(json.dumps({
        "run_id": data.get("run_id"),
        "mode": data.get("mode"),
        "delivery_tier": data.get("delivery_tier", "deep"),
        "intent_routing": data.get("intent_routing"),
        "completed_phases": completed,
        "research_issue_url": data.get("research_issue_url"),
        "implementation_issue_url": data.get("implementation_issue_url"),
        "pull_request_url": data.get("pull_request_url"),
        "stop_before_phases": policy.get("stop_before_phases", []),
        "unresolved_hard_stops": data.get("unresolved_hard_stops", []),
        "latest_transition_judgment": latest,
        "next_invocation": data.get("next_invocation"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
