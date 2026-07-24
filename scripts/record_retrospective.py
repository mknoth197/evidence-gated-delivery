#!/usr/bin/env python3
"""Record fixed-rubric phase retrospectives and detect score degradation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

RUBRIC = {
    "evidence_integrity": 20,
    "external_action_verification": 20,
    "workstream_identity": 15,
    "phase_contract_compliance": 15,
    "semantic_and_privacy_safety": 15,
    "delivery_reliability": 10,
    "learning_quality": 5,
}


def score_total(scorecard: dict[str, object]) -> float:
    if set(scorecard) != set(RUBRIC):
        missing = sorted(set(RUBRIC) - set(scorecard))
        extra = sorted(set(scorecard) - set(RUBRIC))
        raise ValueError(f"scorecard keys mismatch; missing={missing}, extra={extra}")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or v > 4 for v in scorecard.values()):
        raise ValueError("every rubric score must be a number from 0 through 4")
    return round(sum(RUBRIC[key] * float(scorecard[key]) / 4 for key in RUBRIC), 2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--phase", required=True, choices=("research", "plan", "implement", "review"))
    parser.add_argument("--input", required=True, type=Path, help="Auditor JSON with agent_id, scorecard, evidence, findings, remediation_actions")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    incoming = json.loads(args.input.read_text())
    total = score_total(incoming.get("scorecard", {}))
    evidence = incoming.get("evidence")
    if not isinstance(evidence, dict) or any(not isinstance(evidence.get(key), list) or not evidence[key] for key in RUBRIC):
        raise ValueError("evidence must include a non-empty list for every rubric dimension")
    history_path = Path.home() / ".codex" / "evidence-gated-delivery-history" / "retrospectives.jsonl"
    baseline = None
    if history_path.exists():
        for line in reversed(history_path.read_text().splitlines()):
            previous = json.loads(line)
            if previous.get("workflow_version") == manifest.get("workflow_version") and previous.get("phase") == args.phase:
                baseline = previous.get("total")
                break
    delta = None if not isinstance(baseline, (int, float)) else round(total - baseline, 2)
    degraded = delta is not None and delta <= -5
    remediation = incoming.get("remediation_actions", [])
    if (total < 85 or incoming["scorecard"]["evidence_integrity"] < 3 or incoming["scorecard"]["external_action_verification"] < 3 or degraded) and not remediation:
        raise ValueError("below-threshold or degraded retrospective requires remediation_actions")
    entry = {
        "phase": args.phase,
        "agent_id": incoming.get("agent_id", ""),
        "status": "completed",
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scorecard": incoming["scorecard"],
        "evidence": evidence,
        "total": total,
        "baseline_total": baseline,
        "delta": delta,
        "degradation_detected": degraded,
        "findings": incoming.get("findings", []),
        "remediation_actions": remediation,
        "remediation_rechecked": incoming.get("remediation_rechecked", False),
    }
    manifest.setdefault("phase_retrospectives", [])
    manifest["phase_retrospectives"] = [v for v in manifest["phase_retrospectives"] if v.get("phase") != args.phase] + [entry]
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n")
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a") as history:
        history.write(json.dumps({"workflow_version": manifest.get("workflow_version"), **entry}) + "\n")
    print(json.dumps(entry, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
