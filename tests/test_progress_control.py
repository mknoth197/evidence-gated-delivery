from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from progress_control import assess, context_capsule
from context_capsule import create


def run(root: Path):
    path = root / "capsule.json"
    capsule = create(
        path,
        capsule_id="progress-test",
        objective="Make progress",
        settled_decisions=[],
        source_revisions=[],
        evidence_refs=[],
        execution_frontier={"state": "ready", "next_action": "Repair duplicate", "responsible_component": "progress-control", "blocker_ref": None},
        unresolved_questions=[],
        next_action={"description": "Repair duplicate", "risk_classification": "ordinary_scoped_recoverable", "authority_ref": "test-authority"},
        assurance={"requested": "balanced", "effective": "light", "achieved": "light", "selection_origin": "legacy_tier", "legacy_subprofile": "balanced"},
    )
    return {"goal": "Make progress", "delivery_tier": "balanced", "intent_routing": {"progress_corridor": {"continue_without_prompt": ["repair"]}}, "execution_frontier": {"next_material_action": "Repair duplicate", "state": "ready", "recovery_state": "repair"}, "progress_events": [{"kind": "readback", "action": "inspect", "state_changed": False, "evidence_delta": True}], "gate_inventory": [{"name": "graph write", "risk": "wrong relationship", "trigger": "protected write", "cost": "one readback", "review_at": "2026-08-01"}], "context_capsule_ref": {"schema_version": capsule["schema_version"], "capsule_id": capsule["capsule_id"], "generation": capsule["generation"], "digest": capsule["digest"], "locator": str(path)}}


class ProgressControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_valid_frontier_has_evidence_budget(self):
        self.assertEqual(assess(run(self.root))["evidence_budget"], {"sources": 2, "checks": 1})

    def test_repeated_non_progress_is_a_stall(self):
        data = run(self.root)
        data["progress_events"] = [{"kind": "blocked", "action": "ask", "blocker": "same", "state_changed": False, "evidence_delta": False}] * 2
        self.assertTrue(assess(data)["stall_signatures"])

    def test_escalation_requires_real_boundary(self):
        data = run(self.root)
        data["execution_frontier"] = {"next_material_action": "Deploy", "state": "blocked", "recovery_state": "escalate"}
        self.assertTrue(any("hard_boundary" in e for e in assess(data)["errors"]))

    def test_capsule_carries_frontier_and_charter(self):
        capsule = context_capsule(run(self.root), {"objective": "Inspect"})
        self.assertEqual(capsule["frontier"]["next_material_action"], "Repair duplicate")
        self.assertEqual(capsule["capsule"]["status"], "VALID")
        self.assertEqual(capsule["charter"]["objective"], "Inspect")

    def test_gate_economics_overlap_is_diagnostic_not_blocking(self):
        data = run(self.root)
        gate = {
            "schema_version": "gate-economics/v1",
            "gate_id": "audit-a",
            "name": "Audit A",
            "applicability_predicate": "assurance == heavy",
            "applicable": True,
            "failure_class": "same_failure",
            "expected_latency_ms": 10,
            "actual_latency_ms": 8,
            "cost_proxy": "one check",
            "finding": {"status": "no_finding", "finding_ids": []},
            "remediation": None,
            "raw_denominator": 1,
            "duplicate_finding_count": 0,
            "duplicate_finding_rate": 0.0,
            "downstream_outcome": "INSUFFICIENT_EVIDENCE",
            "status": "active",
        }
        second = dict(gate, gate_id="audit-b", name="Audit B")
        data["gate_economics"] = [gate, second]
        result = assess(data)
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(len(result["gate_economics_diagnostics"]), 1)


if __name__ == "__main__":
    unittest.main()
