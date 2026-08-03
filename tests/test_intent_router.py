from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from intent_router import HARD_STOP_ACTIONS, LOW_RISK_ACTIONS, route
from validate_tier import errors_for


class IntentRouterTests(unittest.TestCase):
    def test_small_reversible_work_is_quick(self):
        decision = route({"scope": "low", "ambiguity": "low", "reversibility": "low", "data_risk": "low", "novelty": "low", "external_impact": "none", "hard_stops": []})
        self.assertEqual(decision["tier"], "quick")
        self.assertEqual(decision["authority_envelope"]["ordinary_scoped_work"], list(LOW_RISK_ACTIONS))
        self.assertEqual(decision["progress_corridor"]["continue_without_prompt"], list(LOW_RISK_ACTIONS))

    def test_review_branch_and_scoped_issue_publication_need_no_new_prompt(self):
        decision = route({"external_impact": "ordinary", "hard_stops": []})
        allowed = decision["progress_corridor"]["continue_without_prompt"]
        self.assertIn("push_review_branch", allowed)
        self.assertIn("open_or_update_pull_request", allowed)
        self.assertIn("publish_scoped_issue", allowed)
        self.assertEqual(decision["tier"], "quick")

    def test_uncertain_multi_surface_work_is_balanced(self):
        decision = route({"scope": "medium", "ambiguity": "medium", "reversibility": "low", "data_risk": "low", "novelty": "medium", "external_impact": "ordinary", "hard_stops": []})
        self.assertEqual(decision["tier"], "balanced")

    def test_hard_stop_forces_deep(self):
        decision = route({"hard_stops": ["production_or_release"]})
        self.assertEqual(decision["tier"], "deep")
        self.assertIn("production_or_release", decision["authority_envelope"]["requires_explicit"])

    def test_every_named_hard_stop_forces_deep_and_explicit_authority(self):
        for hard_stop in HARD_STOP_ACTIONS:
            with self.subTest(hard_stop=hard_stop):
                decision = route({"hard_stops": [hard_stop]})
                self.assertEqual(decision["tier"], "deep")
                self.assertIn(hard_stop, decision["progress_corridor"]["pause_only_for"])

    def test_unknown_hard_stop_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unknown hard stops"):
            route({"hard_stops": ["probably_safe"]})

    def test_requested_higher_tier_is_honored(self):
        decision = route({"hard_stops": []}, requested_tier="balanced")
        self.assertEqual(decision["tier"], "balanced")

    def test_malformed_hard_stops_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "array of strings"):
            route({"hard_stops": "production_or_release"})

    def test_balanced_receipt_requires_contract_and_two_sources(self):
        routing = route({"scope": "medium", "ambiguity": "medium", "reversibility": "low", "data_risk": "low", "novelty": "medium", "external_impact": "ordinary", "hard_stops": []})
        data = {"delivery_tier": "balanced", "intent_routing": routing, "tier_evidence": {"sources": ["source"], "checks": ["test"]}}
        errors = errors_for(data)
        self.assertTrue(any("sources" in error for error in errors))
        self.assertTrue(any("contract" in error for error in errors))

    def test_quick_receipt_accepts_targeted_evidence(self):
        routing = route({"hard_stops": []})
        data = {"delivery_tier": "quick", "intent_routing": routing, "tier_evidence": {"sources": ["source"], "checks": ["test"], "external_actions": []}}
        self.assertEqual(errors_for(data), [])

    def test_balanced_receipt_accepts_contract_and_current_evidence(self):
        routing = route({"scope": "medium", "ambiguity": "medium", "reversibility": "low", "data_risk": "low", "novelty": "medium", "external_impact": "ordinary", "hard_stops": []})
        data = {"delivery_tier": "balanced", "intent_routing": routing, "tier_evidence": {"contract": "Bounded change with a targeted verification.", "sources": ["current source", "current test"], "checks": ["targeted test"], "external_actions": []}}
        self.assertEqual(errors_for(data), [])


if __name__ == "__main__":
    unittest.main()
