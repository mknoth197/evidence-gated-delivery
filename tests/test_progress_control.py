from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from progress_control import assess, context_capsule


def run():
    return {"goal": "Make progress", "delivery_tier": "balanced", "intent_routing": {"progress_corridor": {"continue_without_prompt": ["repair"]}}, "execution_frontier": {"next_material_action": "Repair duplicate", "state": "ready", "recovery_state": "repair"}, "progress_events": [{"kind": "readback", "action": "inspect", "state_changed": False, "evidence_delta": True}], "gate_inventory": [{"name": "graph write", "risk": "wrong relationship", "trigger": "protected write", "cost": "one readback", "review_at": "2026-08-01"}]}


class ProgressControlTests(unittest.TestCase):
    def test_valid_frontier_has_evidence_budget(self):
        self.assertEqual(assess(run())["evidence_budget"], {"sources": 2, "checks": 1})

    def test_repeated_non_progress_is_a_stall(self):
        data = run()
        data["progress_events"] = [{"kind": "blocked", "action": "ask", "blocker": "same", "state_changed": False, "evidence_delta": False}] * 2
        self.assertTrue(assess(data)["stall_signatures"])

    def test_escalation_requires_real_boundary(self):
        data = run()
        data["execution_frontier"] = {"next_material_action": "Deploy", "state": "blocked", "recovery_state": "escalate"}
        self.assertTrue(any("hard_boundary" in e for e in assess(data)["errors"]))

    def test_capsule_carries_frontier_and_charter(self):
        capsule = context_capsule(run(), {"objective": "Inspect"})
        self.assertEqual(capsule["frontier"]["next_material_action"], "Repair duplicate")
        self.assertEqual(capsule["charter"]["objective"], "Inspect")


if __name__ == "__main__":
    unittest.main()
