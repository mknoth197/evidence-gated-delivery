#!/usr/bin/env python3
"""Validate proportionate Quick and Balanced delivery receipts; delegate Deep unchanged."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from intent_router import TIERS, route


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
    if tier == "deep":
        return errors
    evidence = data.get("tier_evidence")
    if not isinstance(evidence, dict):
        return errors + ["tier_evidence is required for Quick or Balanced delivery"]
    if not isinstance(evidence.get("sources"), list) or len(evidence["sources"]) < (1 if tier == "quick" else 2):
        errors.append(f"{tier} requires sufficient current evidence sources")
    if not isinstance(evidence.get("checks"), list) or not evidence["checks"]:
        errors.append(f"{tier} requires at least one recorded check")
    actions = evidence.get("external_actions", [])
    if not isinstance(actions, list):
        errors.append("tier_evidence.external_actions must be an array")
    elif any(not isinstance(action, dict) or action.get("state") != "verified" for action in actions):
        errors.append("claimed external actions must be verified")
    if tier == "balanced" and not isinstance(evidence.get("contract"), str):
        errors.append("Balanced delivery requires a concise contract")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--phase", choices=("research", "plan", "implement", "review"))
    args = parser.parse_args()
    data = json.loads(args.manifest.read_text())
    if data.get("delivery_tier") == "deep":
        command = [sys.executable, str(Path(__file__).with_name("validate_run.py")), str(args.manifest)]
        if args.phase:
            command.extend(["--phase", args.phase])
        return subprocess.run(command, check=False).returncode
    errors = errors_for(data)
    print(json.dumps({"status": "VALID" if not errors else "INVALID", "tier": data.get("delivery_tier"), "errors": errors}, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
