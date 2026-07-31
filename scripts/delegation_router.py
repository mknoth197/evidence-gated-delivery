#!/usr/bin/env python3
"""Choose a proportionate orchestration topology and validate child-task charters."""

from __future__ import annotations

import argparse
import json
from typing import Any


TOPOLOGIES = {"solo", "one_off", "parallel_workstreams", "phase_isolated"}
HARD_BOUNDARIES = {
    "protected_external_write",
    "destructive_or_irreversible",
    "production_or_release",
    "sensitive_data_access",
}


def choose(
    *,
    tier: str,
    independent_outcomes: int = 0,
    shared_state: str = "low",
    external_wait: bool = False,
    independent_challenge: bool = False,
) -> dict[str, Any]:
    """Select only threads whose independent outcome repays coordination cost."""
    if tier not in {"quick", "balanced", "deep"}:
        raise ValueError("tier must be quick, balanced, or deep")
    if independent_outcomes < 0:
        raise ValueError("independent_outcomes must be non-negative")
    if shared_state not in {"low", "medium", "high"}:
        raise ValueError("shared_state must be low, medium, or high")
    if tier == "quick":
        topology = "one_off" if external_wait or independent_outcomes == 1 else "solo"
        roles = ["investigator"] if topology == "one_off" else []
    elif tier == "balanced":
        if shared_state == "high":
            topology, roles = "solo", []
        elif independent_outcomes >= 2:
            topology, roles = "parallel_workstreams", ["workstream_owner"] * min(independent_outcomes, 3)
        elif independent_outcomes == 1 or external_wait:
            topology, roles = "one_off", ["investigator"]
        else:
            topology, roles = "solo", []
    else:
        topology = "phase_isolated"
        roles = ["researcher", "implementer", "independent_reviewer"]
    if independent_challenge and "independent_reviewer" not in roles:
        roles.append("independent_reviewer")
    return {
        "topology": topology,
        "max_threads": len(roles),
        "roles": roles,
        "synthesis_required": bool(roles),
        "rule": "delegate only independent outcomes; integrate before another cohort",
    }


def validate_charter(charter: dict[str, Any], progress_corridor: dict[str, Any]) -> list[str]:
    """Reject vague delegation and authority expansion by a child task."""
    required = ("objective", "role", "owned_scope", "inputs", "completion_evidence", "escalation_conditions")
    errors = [f"delegation charter requires {field}" for field in required if not charter.get(field)]
    allowed = charter.get("allowed_actions")
    corridor_actions = set(progress_corridor.get("continue_without_prompt", []))
    if not isinstance(allowed, list) or not allowed:
        errors.append("delegation charter requires allowed_actions")
    elif not set(allowed).issubset(corridor_actions):
        errors.append("delegation charter expands the parent progress corridor")
    if set(allowed or []) & HARD_BOUNDARIES:
        errors.append("delegation charter cannot grant a hard-boundary action")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", required=True, choices=("quick", "balanced", "deep"))
    parser.add_argument("--independent-outcomes", type=int, default=0)
    parser.add_argument("--shared-state", choices=("low", "medium", "high"), default="low")
    parser.add_argument("--external-wait", action="store_true")
    parser.add_argument("--independent-challenge", action="store_true")
    args = parser.parse_args()
    print(json.dumps(choose(tier=args.tier, independent_outcomes=args.independent_outcomes, shared_state=args.shared_state, external_wait=args.external_wait, independent_challenge=args.independent_challenge), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
