#!/usr/bin/env python3
"""Validate proportionate Quick and Balanced delivery receipts; delegate Deep unchanged."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from intent_router import TIERS, resolve_assurance_invocation, route
from risk_floor import evaluate_light_action


def errors_for(data: dict[str, Any]) -> list[str]:
    tier = data.get("delivery_tier")
    routing = data.get("intent_routing")
    if tier not in TIERS:
        return ["delivery_tier must be quick, balanced, or deep"]
    if not isinstance(routing, dict) or not isinstance(routing.get("evidence"), dict):
        return ["intent_routing.evidence is required"]
    try:
        expected = route(routing["evidence"], requested_tier=routing.get("requested_tier"))
    except ValueError as error:
        return [str(error)]
    errors: list[str] = []
    if expected["tier"] != tier or routing.get("risk_score") != expected["risk_score"]:
        errors.append("intent routing decision does not match its evidence")
    if routing.get("authority_envelope") != expected["authority_envelope"]:
        errors.append("authority envelope does not match the routing decision")
    assurance = data.get("assurance")
    if not isinstance(assurance, dict):
        errors.append("assurance decision is required")
        effective_assurance = None
    else:
        effective_assurance = assurance.get("effective_assurance")
        explicit = assurance.get("selection_origin") == "explicit_assurance"
        try:
            expected_assurance = resolve_assurance_invocation(
                ["--assurance", str(assurance.get("requested_assurance")), str(data.get("mode"))]
                if explicit
                else [],
                inferred_mode=None if explicit else str(data.get("mode") or "research"),
                legacy_tier=None if explicit else tier,
            )
        except ValueError as error:
            errors.append(str(error))
            expected_assurance = None
        if assurance != expected_assurance:
            errors.append("assurance decision does not match its selection origin")
    if effective_assurance == "heavy":
        return errors
    evidence = data.get("tier_evidence")
    if not isinstance(evidence, dict):
        return errors + ["tier_evidence is required for Quick or Balanced delivery"]
    legacy_subprofile = assurance.get("legacy_subprofile") if isinstance(assurance, dict) else None
    action_class = evidence.get("action_class", "inspect")
    boundaries = expected.get("hard_floor_reasons", [])
    risk_decision = evaluate_light_action(
        action_class,
        hard_boundaries=boundaries,
        evidence=["intent_routing.evidence"],
        added_heavy_controls=["Heavy phase receipt and required independent gates"],
        estimated_incremental_cost="Heavy assurance gate bundle",
    )
    if risk_decision["status"] != "proceed":
        errors.append(
            f"Light assurance blocked by risk floor: {risk_decision['code']}"
            + (f" ({risk_decision['rule_id']})" if risk_decision.get("rule_id") else "")
        )
    required_sources = 2 if legacy_subprofile == "balanced" else 1
    label = legacy_subprofile or "light"
    if not isinstance(evidence.get("sources"), list) or len(evidence["sources"]) < required_sources:
        errors.append(f"{label} requires sufficient current evidence sources")
    if not isinstance(evidence.get("checks"), list) or not evidence["checks"]:
        errors.append(f"{label} requires at least one recorded check")
    actions = evidence.get("external_actions", [])
    if not isinstance(actions, list):
        errors.append("tier_evidence.external_actions must be an array")
    elif any(not isinstance(action, dict) or action.get("state") != "verified" for action in actions):
        errors.append("claimed external actions must be verified")
    if legacy_subprofile == "balanced" and not isinstance(evidence.get("contract"), str):
        errors.append("Balanced delivery requires a concise contract")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--phase", choices=("research", "plan", "implement", "review"))
    args = parser.parse_args()
    data = json.loads(args.manifest.read_text())
    assurance = data.get("assurance")
    if isinstance(assurance, dict) and assurance.get("effective_assurance") == "heavy":
        command = [sys.executable, str(Path(__file__).with_name("validate_run.py")), str(args.manifest)]
        if args.phase:
            command.extend(["--phase", args.phase])
        return subprocess.run(command, check=False).returncode
    errors = errors_for(data)
    print(json.dumps({"status": "VALID" if not errors else "INVALID", "tier": data.get("delivery_tier"), "errors": errors}, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
