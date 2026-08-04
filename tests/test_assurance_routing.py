from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from intent_router import HARD_STOP_ACTIONS, LOW_RISK_ACTIONS, resolve_assurance_invocation, route
from risk_floor import NEXT_ACTIONS, evaluate_light_action
from validate_tier import errors_for
import validate_tier


class AssuranceRoutingTests(unittest.TestCase):
    def test_explicit_selector_is_separate_from_mode(self):
        resolved = resolve_assurance_invocation(["--assurance", "light", "implement", "issue"])
        self.assertEqual(resolved["mode"], "implement")
        self.assertEqual(resolved["effective_assurance"], "light")
        self.assertEqual(resolved["selection_origin"], "explicit_assurance")

    def test_phase_and_inferred_defaults_preserve_heavy(self):
        phase = resolve_assurance_invocation(["plan", "issue"])
        inferred = resolve_assurance_invocation(["goal"], inferred_mode="research")
        self.assertEqual((phase["effective_assurance"], phase["selection_origin"]), ("heavy", "legacy_phase_command"))
        self.assertEqual((inferred["effective_assurance"], inferred["selection_origin"]), ("heavy", "legacy_inferred_command"))
        self.assertEqual(phase["requested_assurance"], "heavy")

    def test_legacy_tiers_map_without_silent_downgrade(self):
        for tier, effective in (("quick", "light"), ("balanced", "light"), ("deep", "heavy")):
            with self.subTest(tier=tier):
                resolved = resolve_assurance_invocation([], inferred_mode="implement", legacy_tier=tier)
                self.assertEqual(resolved["effective_assurance"], effective)
                self.assertEqual(resolved["legacy_subprofile"], tier)
                self.assertEqual(resolved["selection_origin"], "legacy_tier")
        quick_mode = resolve_assurance_invocation(["quick"], legacy_tier="quick")
        self.assertEqual((quick_mode["mode"], quick_mode["effective_assurance"]), ("quick", "light"))

    def test_invalid_selector_combinations_fail_closed(self):
        invalid = (
            (["--assurance"], None),
            (["--assurance", "cheap", "plan"], None),
            (["plan", "--assurance", "light"], None),
            (["--assurance", "light", "status"], None),
            (["--assurance", "light", "plan", "heavy"], None),
            (["--assurance", "light", "plan", "--assurance", "heavy"], None),
            (["--assurance", "light", "unknown-mode"], None),
        )
        for tokens, inferred in invalid:
            with self.subTest(tokens=tokens), self.assertRaisesRegex(ValueError, "BLOCKED_ASSURANCE_SELECTION"):
                resolve_assurance_invocation(tokens, inferred_mode=inferred)

    def test_every_corridor_action_is_allowed_and_external_actions_require_readback(self):
        for action in LOW_RISK_ACTIONS:
            with self.subTest(action=action):
                result = evaluate_light_action(action)
                self.assertEqual(result["status"], "proceed")
        self.assertTrue(evaluate_light_action("open_pull_request")["requires_remote_readback"])

    def test_every_hard_boundary_emits_closed_blocker(self):
        for boundary in HARD_STOP_ACTIONS:
            with self.subTest(boundary=boundary):
                result = evaluate_light_action(
                    "inspect",
                    hard_boundaries=[boundary],
                    evidence=["E-1"],
                    added_heavy_controls=["audit"],
                    estimated_incremental_cost="one audit",
                )
                self.assertEqual(result["code"], "BLOCKED_REQUIRED_ESCALATION")
                self.assertEqual(result["rule_id"], boundary)
                self.assertEqual(result["next_actions"], list(NEXT_ACTIONS))

    def test_unknown_or_contradictory_classification_fails_closed(self):
        unknown = evaluate_light_action("probably_safe")
        contradictory = evaluate_light_action("inspect", classification_consistent=False)
        self.assertEqual(unknown["code"], "BLOCKED_ASSURANCE_SELECTION")
        self.assertEqual(contradictory["classification_error"], "contradictory")

    def test_explicit_light_blocks_hard_boundary_even_with_legacy_deep_field(self):
        assurance = resolve_assurance_invocation(["--assurance", "light", "implement"])
        data = {
            "mode": "implement",
            "delivery_tier": "deep",
            "assurance": assurance,
            "intent_routing": route({"hard_stops": ["material_architecture_ambiguity"]}),
            "tier_evidence": {"sources": ["current source"], "checks": ["targeted test"], "external_actions": []},
        }
        errors = errors_for(data)
        self.assertTrue(any("BLOCKED_REQUIRED_ESCALATION" in error for error in errors))

    def test_explicit_light_uses_light_evidence_without_a_hard_boundary(self):
        assurance = resolve_assurance_invocation(["--assurance", "light", "implement"])
        data = {
            "mode": "implement",
            "delivery_tier": "quick",
            "assurance": assurance,
            "intent_routing": route({}),
            "tier_evidence": {
                "action_class": "test",
                "sources": ["current source"],
                "checks": ["targeted test"],
                "external_actions": [],
            },
        }
        self.assertEqual(errors_for(data), [])

    def test_every_explicit_phase_and_inferred_mode_has_deterministic_default(self):
        for mode in ("research", "plan", "implement", "review", "orchestrate"):
            with self.subTest(mode=mode):
                explicit = resolve_assurance_invocation(["--assurance", "light", mode])
                inferred = resolve_assurance_invocation([], inferred_mode=mode)
                self.assertEqual((explicit["mode"], explicit["effective_assurance"]), (mode, "light"))
                self.assertEqual((inferred["mode"], inferred["effective_assurance"]), (mode, "heavy"))

    def test_declined_escalation_can_narrow_to_the_progress_corridor(self):
        blocked = evaluate_light_action(
            "inspect",
            hard_boundaries=["material_architecture_ambiguity"],
            estimated_incremental_cost="one independent gate",
        )
        narrowed = evaluate_light_action("inspect")
        self.assertEqual(blocked["next_actions"], ["authorize", "narrow", "stop"])
        self.assertEqual(blocked["estimated_incremental_cost"], "one independent gate")
        self.assertEqual(narrowed["status"], "proceed")

    def test_explicit_heavy_and_legacy_deep_preserve_the_same_gate_class(self):
        explicit = resolve_assurance_invocation(["--assurance", "heavy", "implement"])
        legacy = resolve_assurance_invocation([], inferred_mode="implement", legacy_tier="deep")
        self.assertEqual(explicit["effective_assurance"], "heavy")
        self.assertEqual(legacy["effective_assurance"], "heavy")
        self.assertEqual(explicit["achieved_assurance"], legacy["achieved_assurance"])

    def test_explicit_heavy_and_legacy_deep_dispatch_to_the_same_validator(self):
        explicit = {
            "delivery_tier": "deep",
            "assurance": resolve_assurance_invocation(["--assurance", "heavy", "implement"]),
        }
        legacy = {
            "delivery_tier": "deep",
            "assurance": resolve_assurance_invocation([], inferred_mode="implement", legacy_tier="deep"),
        }
        with tempfile.TemporaryDirectory() as directory:
            commands = []
            for label, manifest in (("explicit", explicit), ("legacy", legacy)):
                path = Path(directory) / f"{label}.json"
                path.write_text(json.dumps(manifest))
                with patch.object(sys, "argv", ["validate_tier.py", str(path), "--phase", "implement"]), patch.object(
                    validate_tier.subprocess,
                    "run",
                    return_value=type("Result", (), {"returncode": 0})(),
                ) as delegated:
                    self.assertEqual(validate_tier.main(), 0)
                    commands.append(delegated.call_args.args[0][:-2] + delegated.call_args.args[0][-2:])
            self.assertEqual(commands[0][0], commands[1][0])
            self.assertEqual(commands[0][1], commands[1][1])
            self.assertEqual(Path(commands[0][1]).name, "validate_run.py")
            self.assertEqual(commands[0][-2:], ["--phase", "implement"])
            self.assertEqual(commands[1][-2:], ["--phase", "implement"])


if __name__ == "__main__":
    unittest.main()
