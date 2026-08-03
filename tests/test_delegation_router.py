from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from delegation_router import choose, validate_charter


CORRIDOR = {"continue_without_prompt": ["inspect", "edit", "test", "reconcile", "repair", "branch", "commit", "push_review_branch", "open_or_update_pull_request", "publish_scoped_issue", "read_back"]}


class DelegationRouterTests(unittest.TestCase):
    def test_quick_stays_solo_without_an_independent_outcome(self):
        self.assertEqual(choose(tier="quick")["topology"], "solo")

    def test_balanced_fans_out_independent_workstreams(self):
        decision = choose(tier="balanced", independent_outcomes=2)
        self.assertEqual(decision["topology"], "parallel_workstreams")
        self.assertTrue(decision["synthesis_required"])

    def test_shared_state_keeps_balanced_work_central(self):
        self.assertEqual(choose(tier="balanced", independent_outcomes=3, shared_state="high")["topology"], "solo")

    def test_deep_uses_phase_isolation(self):
        self.assertEqual(choose(tier="deep")["topology"], "phase_isolated")

    def test_charter_rejects_authority_expansion(self):
        charter = {"objective": "Inspect", "role": "investigator", "owned_scope": ["x"], "inputs": ["source"], "allowed_actions": ["deploy"], "completion_evidence": ["report"], "escalation_conditions": ["hard boundary"]}
        self.assertTrue(any("expands" in error for error in validate_charter(charter, CORRIDOR)))

    def test_complete_charter_inherits_corridor(self):
        charter = {"objective": "Inspect", "role": "investigator", "owned_scope": ["x"], "inputs": ["source"], "allowed_actions": ["inspect", "test"], "completion_evidence": ["report"], "escalation_conditions": ["hard boundary"]}
        self.assertEqual(validate_charter(charter, CORRIDOR), [])


if __name__ == "__main__":
    unittest.main()
