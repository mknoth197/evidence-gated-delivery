#!/usr/bin/env python3
"""Evaluate Light assurance actions against the closed progress corridor."""

from __future__ import annotations

import argparse
import json
from typing import Any

from intent_router import HARD_STOP_ACTIONS, LOW_RISK_ACTIONS

NEXT_ACTIONS = ("authorize", "narrow", "stop")


def evaluate_light_action(
    action_class: str,
    *,
    hard_boundaries: list[str] | None = None,
    evidence: list[str] | None = None,
    added_heavy_controls: list[str] | None = None,
    estimated_incremental_cost: str = "unknown",
    classification_consistent: bool = True,
) -> dict[str, Any]:
    """Return a deterministic proceed decision or an evidence-bearing blocker."""

    boundaries = hard_boundaries or []
    if not isinstance(boundaries, list) or not all(isinstance(value, str) for value in boundaries):
        raise ValueError("hard_boundaries must be an array of strings")
    unknown = sorted(set(boundaries) - HARD_STOP_ACTIONS)
    if unknown or action_class not in LOW_RISK_ACTIONS or not classification_consistent:
        return {
            "status": "blocked",
            "code": "BLOCKED_ASSURANCE_SELECTION",
            "classification_error": "unknown" if unknown or action_class not in LOW_RISK_ACTIONS else "contradictory",
            "evidence": list(evidence or []),
            "next_actions": list(NEXT_ACTIONS),
        }
    if not boundaries:
        return {
            "status": "proceed",
            "code": None,
            "action_class": action_class,
            "requires_remote_readback": action_class
            in {"publish_review_branch", "open_pull_request", "update_scoped_issue", "publish_deterministic_graph"},
        }
    rule_id = boundaries[0]
    return {
        "status": "blocked",
        "code": "BLOCKED_REQUIRED_ESCALATION",
        "rule_id": rule_id,
        "evidence": list(evidence or []),
        "added_heavy_controls": list(added_heavy_controls or []),
        "estimated_incremental_cost": estimated_incremental_cost,
        "next_actions": list(NEXT_ACTIONS),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action_class")
    parser.add_argument("--hard-boundary", action="append", default=[])
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--heavy-control", action="append", default=[])
    parser.add_argument("--estimated-incremental-cost", default="unknown")
    parser.add_argument("--contradictory-classification", action="store_true")
    args = parser.parse_args()
    result = evaluate_light_action(
        args.action_class,
        hard_boundaries=args.hard_boundary,
        evidence=args.evidence,
        added_heavy_controls=args.heavy_control,
        estimated_incremental_cost=args.estimated_incremental_cost,
        classification_consistent=not args.contradictory_classification,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "proceed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
