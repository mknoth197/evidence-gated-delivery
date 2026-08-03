#!/usr/bin/env python3
"""Select a proportionate delivery tier and authority envelope from observed risk."""

from __future__ import annotations

import argparse
import json
from typing import Any


TIERS = ("quick", "balanced", "deep")
TIER_RANK = {tier: index for index, tier in enumerate(TIERS)}
LEVELS = {"low": 0, "medium": 1, "high": 2}
EXTERNAL_IMPACT = {"none": 0, "ordinary": 1, "protected": 2}
HARD_STOP_ACTIONS = {
    "protected_external_write",
    "destructive_or_irreversible",
    "production_or_release",
    "sensitive_data_access",
    "missing_authority",
    "material_architecture_ambiguity",
}
LOW_RISK_ACTIONS = (
    "inspect",
    "edit",
    "test",
    "reconcile",
    "repair",
    "branch",
    "commit",
    "push_review_branch",
    "open_or_update_pull_request",
    "publish_scoped_issue",
    "read_back",
)


def route(
    evidence: dict[str, str], *, requested_tier: str | None = None
) -> dict[str, Any]:
    """Return a reproducible tier decision from a small, inspectable risk record."""
    unknown = set(evidence) - {"scope", "ambiguity", "reversibility", "data_risk", "novelty", "external_impact", "hard_stops"}
    if unknown:
        raise ValueError(f"unknown routing evidence: {', '.join(sorted(unknown))}")
    levels = {
        key: evidence.get(key, "low")
        for key in ("scope", "ambiguity", "reversibility", "data_risk", "novelty")
    }
    invalid = {key: value for key, value in levels.items() if value not in LEVELS}
    external_impact = evidence.get("external_impact", "none")
    if external_impact not in EXTERNAL_IMPACT:
        invalid["external_impact"] = external_impact
    if invalid:
        raise ValueError(f"invalid routing levels: {invalid}")
    raw_hard_stops = evidence.get("hard_stops", [])
    if not isinstance(raw_hard_stops, list) or not all(
        isinstance(value, str) for value in raw_hard_stops
    ):
        raise ValueError("hard_stops must be an array of strings")
    hard_stops = sorted(set(raw_hard_stops))
    invalid_stops = sorted(set(hard_stops) - HARD_STOP_ACTIONS)
    if invalid_stops:
        raise ValueError(f"unknown hard stops: {', '.join(invalid_stops)}")
    score = sum(LEVELS[value] for value in levels.values()) + EXTERNAL_IMPACT[external_impact]
    selected = "quick" if score <= 2 else "balanced" if score <= 7 else "deep"
    floor_reasons = list(hard_stops)
    if external_impact == "protected":
        floor_reasons.append("protected_external_write")
    if floor_reasons:
        selected = "deep"
    if requested_tier is not None:
        if requested_tier not in TIER_RANK:
            raise ValueError(f"requested_tier must be one of: {', '.join(TIERS)}")
        if TIER_RANK[requested_tier] > TIER_RANK[selected]:
            selected = requested_tier
    return {
        "tier": selected,
        "risk_score": score,
        "evidence": {**levels, "external_impact": external_impact, "hard_stops": hard_stops},
        "hard_floor_reasons": sorted(set(floor_reasons)),
        "authority_envelope": {
            "ordinary_scoped_work": list(LOW_RISK_ACTIONS),
            "requires_explicit": sorted(HARD_STOP_ACTIONS),
        },
        "progress_corridor": {
            "continue_without_prompt": list(LOW_RISK_ACTIONS),
            "pause_only_for": sorted(HARD_STOP_ACTIONS),
        },
        "rationale": (
            "hard safety floor applies" if floor_reasons else f"risk score {score} selects {selected}"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requested-tier", choices=TIERS)
    for field in ("scope", "ambiguity", "reversibility", "data-risk", "novelty"):
        parser.add_argument(f"--{field}", choices=LEVELS, default="low")
    parser.add_argument("--external-impact", choices=EXTERNAL_IMPACT, default="none")
    parser.add_argument("--hard-stop", action="append", choices=sorted(HARD_STOP_ACTIONS), default=[])
    args = parser.parse_args()
    evidence = {
        "scope": args.scope,
        "ambiguity": args.ambiguity,
        "reversibility": args.reversibility,
        "data_risk": args.data_risk,
        "novelty": args.novelty,
        "external_impact": args.external_impact,
        "hard_stops": args.hard_stop,
    }
    print(json.dumps(route(evidence, requested_tier=args.requested_tier), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
