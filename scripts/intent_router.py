#!/usr/bin/env python3
"""Select a proportionate delivery tier and authority envelope from observed risk."""

from __future__ import annotations

import argparse
import json
from typing import Any


TIERS = ("quick", "balanced", "deep")
TIER_RANK = {tier: index for index, tier in enumerate(TIERS)}
ASSURANCE_LEVELS = ("light", "heavy")
MUTATING_MODES = ("research", "plan", "implement", "review", "orchestrate")
ALL_MODES = (*MUTATING_MODES, "status")
LEGACY_TIER_MODES = ("quick", "balanced")
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
    "repair",
    "branch",
    "commit",
    "publish_review_branch",
    "open_pull_request",
    "update_scoped_issue",
    "publish_deterministic_graph",
    "verify_remote_readback",
)


def _assurance_selection_error(message: str) -> ValueError:
    return ValueError(f"BLOCKED_ASSURANCE_SELECTION: {message}")


def resolve_assurance_invocation(
    tokens: list[str],
    *,
    inferred_mode: str | None = None,
    legacy_tier: str | None = None,
) -> dict[str, Any]:
    """Resolve mode and assurance without allowing an implicit downgrade.

    ``tokens`` are the arguments after ``$evidence-gated-delivery``.  Legacy
    tier selection is an adapter input, not a positional phase token.
    """

    if not isinstance(tokens, list) or not all(isinstance(token, str) for token in tokens):
        raise _assurance_selection_error("invocation tokens must be strings")
    selector_positions = [index for index, token in enumerate(tokens) if token == "--assurance"]
    if len(selector_positions) > 1:
        raise _assurance_selection_error("--assurance may occur only once")
    if selector_positions and selector_positions[0] != 0:
        raise _assurance_selection_error("--assurance must immediately follow the skill name")
    if legacy_tier is not None and selector_positions:
        raise _assurance_selection_error("legacy tier and explicit assurance are mutually exclusive")

    explicit_assurance: str | None = None
    remaining = list(tokens)
    if selector_positions:
        if len(remaining) < 2:
            raise _assurance_selection_error("--assurance requires light or heavy")
        explicit_assurance = remaining[1]
        if explicit_assurance not in ASSURANCE_LEVELS:
            raise _assurance_selection_error("unknown assurance; expected light or heavy")
        remaining = remaining[2:]
    if any(token == "--assurance" or token in ASSURANCE_LEVELS for token in remaining[1:]):
        raise _assurance_selection_error("additional positional assurance tokens are invalid")

    explicit_mode = (
        remaining[0]
        if remaining
        and (
            remaining[0] in ALL_MODES
            or (legacy_tier is not None and remaining[0] in LEGACY_TIER_MODES)
        )
        else None
    )
    mode = explicit_mode or inferred_mode
    if mode not in ALL_MODES and not (
        legacy_tier is not None and mode in LEGACY_TIER_MODES
    ):
        raise _assurance_selection_error("a supported mode is required")
    if explicit_assurance is not None and mode == "status":
        raise _assurance_selection_error("status creates no run and rejects assurance selection")
    if explicit_mode is not None and inferred_mode is not None and explicit_mode != inferred_mode:
        raise _assurance_selection_error("explicit and inferred modes disagree")

    if legacy_tier is not None:
        if legacy_tier not in TIERS:
            raise _assurance_selection_error("legacy tier must be quick, balanced, or deep")
        effective = "heavy" if legacy_tier == "deep" else "light"
        requested = None
        origin = "legacy_tier"
        subprofile = legacy_tier
    elif explicit_assurance is not None:
        effective = explicit_assurance
        requested = explicit_assurance
        origin = "explicit_assurance"
        subprofile = None
    else:
        effective = "heavy" if mode != "status" else None
        requested = "heavy" if mode != "status" else None
        origin = "legacy_phase_command" if explicit_mode is not None else "legacy_inferred_command"
        subprofile = None

    return {
        "mode": mode,
        "requested_assurance": requested,
        "requested_legacy_tier": legacy_tier,
        "effective_assurance": effective,
        "legacy_subprofile": subprofile,
        "selection_origin": origin,
        "achieved_assurance": "pending" if mode != "status" else None,
    }


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
        "assurance": resolve_assurance_invocation([], inferred_mode="research", legacy_tier=selected),
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
