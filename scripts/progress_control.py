#!/usr/bin/env python3
"""Measure whether a run is advancing, stalling, or at a real decision boundary."""

from __future__ import annotations

import argparse
import json
from typing import Any

FRONTIER_STATES = {"ready", "delegated", "verified", "blocked", "retired"}
RECOVERY_STATES = {"continue", "repair", "escalate", "retire"}
EVIDENCE_BUDGETS = {"quick": {"sources": 1, "checks": 1}, "balanced": {"sources": 2, "checks": 1}, "deep": {"sources": 2, "checks": 1}}


def context_capsule(data: dict[str, Any], charter: dict[str, Any]) -> dict[str, Any]:
    """Give a fresh task only the current state needed to create one bounded delta."""
    return {"goal": data.get("goal", ""), "frontier": data.get("execution_frontier", {}), "progress_corridor": data.get("intent_routing", {}).get("progress_corridor", {}), "known_evidence": data.get("progress_evidence", []), "open_questions": data.get("open_questions", []), "charter": charter}


def assess(data: dict[str, Any]) -> dict[str, Any]:
    """Return actionable control signals; repeated non-progress is a workflow failure."""
    errors: list[str] = []
    frontier = data.get("execution_frontier")
    if not isinstance(frontier, dict):
        errors.append("execution_frontier is required")
        frontier = {}
    state, recovery = frontier.get("state"), frontier.get("recovery_state")
    if state not in FRONTIER_STATES:
        errors.append("execution_frontier.state is invalid")
    if recovery not in RECOVERY_STATES:
        errors.append("execution_frontier.recovery_state is invalid")
    if not str(frontier.get("next_material_action", "")).strip():
        errors.append("execution_frontier.next_material_action is required")
    if state == "blocked" and recovery != "escalate":
        errors.append("blocked frontier must use recovery_state escalate")
    if recovery == "escalate" and not frontier.get("hard_boundary"):
        errors.append("escalation requires a named hard_boundary")
    events = data.get("progress_events", [])
    if not isinstance(events, list):
        errors.append("progress_events must be an array")
        events = []
    repeats, previous, no_progress = [], None, 0
    for event in events:
        if not isinstance(event, dict):
            errors.append("progress event must be an object")
            continue
        signature = (event.get("kind"), event.get("action"), event.get("blocker"))
        advances = bool(event.get("state_changed")) or bool(event.get("evidence_delta"))
        no_progress = 0 if advances else no_progress + 1
        if signature == previous and no_progress >= 2:
            repeats.append(str(signature))
        previous = signature
    gates = data.get("gate_inventory", [])
    if not isinstance(gates, list):
        errors.append("gate_inventory must be an array")
        gates = []
    for gate in gates:
        if not isinstance(gate, dict) or any(not gate.get(key) for key in ("name", "risk", "trigger", "cost", "review_at")):
            errors.append("every gate needs name, risk, trigger, cost, and review_at")
    metrics = {"material_actions": sum(bool(e.get("state_changed")) for e in events if isinstance(e, dict)), "evidence_deltas": sum(bool(e.get("evidence_delta")) for e in events if isinstance(e, dict)), "user_interruptions": sum(e.get("kind") == "user_interrupt" for e in events if isinstance(e, dict)), "stalled": bool(repeats)}
    return {"status": "VALID" if not errors and not repeats else "INVALID", "errors": errors, "stall_signatures": repeats, "evidence_budget": EVIDENCE_BUDGETS.get(data.get("delivery_tier", "deep"), EVIDENCE_BUDGETS["deep"]), "metrics": metrics}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    args = parser.parse_args()
    with open(args.manifest, encoding="utf-8") as stream:
        result = assess(json.load(stream))
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "VALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())
