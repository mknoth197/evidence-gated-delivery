from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from intent_router import route
from validate_tier import errors_for


class IntentRouterTests(unittest.TestCase):
    def test_small_reversible_work_is_quick(self):
        decision = route({"scope": "low", "ambiguity": "low", "reversibility": "low", "data_risk": "low", "novelty": "low", "external_impact": "none", "hard_stops": []})
        self.assertEqual(decision["tier"], "quick")
        self.assertEqual(decision["authority_envelope"]["ordinary_scoped_work"], ["inspect", "edit", "test"])

    def test_uncertain_multi_surface_work_is_balanced(self):
        decision = route({"scope": "medium", "ambiguity": "medium", "reversibility": "low", "data_risk": "low", "novelty": "medium", "external_impact": "ordinary", "hard_stops": []})
        self.assertEqual(decision["tier"], "balanced")

    def test_hard_stop_forces_deep(self):
        decision = route({"hard_stops": ["production_or_release"]})
        self.assertEqual(decision["tier"], "deep")
        self.assertIn("production_or_release", decision["authority_envelope"]["requires_explicit"])

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
