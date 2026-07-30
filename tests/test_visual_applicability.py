from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "visual_applicability", ROOT / "scripts" / "visual_applicability.py"
)
assert SPEC and SPEC.loader
visual = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(visual)


def entry(identifier: str, kind: str = "nonvisual", **extra):
    source_by_group = {
        "nonvisual": "validator workflow in scripts/tool.py",
        "runtime": "existing user-visible interface behavior",
        "generative": "new screen visual concept",
    }
    group = (
        "runtime"
        if kind in visual.RUNTIME_KINDS
        else "generative"
        if kind in visual.GENERATIVE_KINDS
        else "nonvisual"
    )
    value = {
        "id": identifier,
        "kind": kind,
        "source": source_by_group[group],
        "provenance": f"evidence for {identifier}",
        **extra,
    }
    if kind in visual.RUNTIME_KINDS and "runtime_evidence_sufficient" not in value:
        value["runtime_evidence_sufficient"] = True
    return value


def base_inventory(kind: str = "nonvisual"):
    scoped_path = (
        "web/NewScreen.tsx"
        if kind in visual.GENERATIVE_KINDS
        else "web/ExistingPanel.tsx"
        if kind in visual.RUNTIME_KINDS
        else "scripts/tool.py"
    )
    return {
        "deliverables": [entry("D-001", kind)],
        "user_directions": [
            entry(
                "UD-001",
                directive="neutral",
                authority="user",
                scope="D-001",
                source_order=1,
                turn="one",
            )
        ],
        "acceptance_criteria": [entry("AC-001", kind)],
        "tasks": [entry("T-001", kind)],
        "affected_modules": [entry("M-001", kind, source=scoped_path)],
        "planned_paths": [entry("P-001", kind, path=scoped_path)],
    }


def declarations(inventory):
    return {
        domain: [item["id"] for item in inventory[domain]]
        for domain in visual.DOMAIN_PREFIXES
        if domain in inventory
    }


def evaluate(inventory, **kwargs):
    return visual.evaluate_visual_applicability(
        inventory,
        phase=kwargs.pop("phase", "plan"),
        authoritative_issue_body=kwargs.pop("body", "authoritative body"),
        declared_ids=kwargs.pop("declared_ids", declarations(inventory)),
        **kwargs,
    )


class ModeSelectionTests(unittest.TestCase):
    def test_backend_cli_validator_and_workflow_docs_are_none(self):
        for kind in ("backend", "cli", "validator", "workflow", "docs_mermaid"):
            with self.subTest(kind=kind):
                receipt = evaluate(base_inventory(kind))
                self.assertEqual(receipt["decision"], "VISUAL_NOT_APPLICABLE")
                self.assertEqual(receipt["evidence_mode"], "none")

    def test_unrelated_frontend_signal_does_not_trigger_visuals(self):
        receipt = evaluate(
            base_inventory("backend"),
            repository_signals=[
                {
                    "path": "web/App.tsx",
                    "signal": "frontend",
                    "provenance": "repository scan",
                }
            ],
        )
        self.assertEqual(receipt["evidence_mode"], "none")
        self.assertEqual(receipt["ignored_repository_signals"][0]["path"], "web/App.tsx")

    def test_existing_ui_changes_use_runtime_capture(self):
        for kind in (
            "ui_copy",
            "aria",
            "focus_behavior",
            "css_regression",
            "existing_component_state",
            "frontend_affecting_contract",
        ):
            with self.subTest(kind=kind):
                self.assertEqual(evaluate(base_inventory(kind))["evidence_mode"], "runtime_capture")

    def test_runtime_sufficiency_must_be_explicit(self):
        inventory = base_inventory("ui_copy")
        for domain in inventory.values():
            for value in domain:
                value.pop("runtime_evidence_sufficient", None)
        self.assertEqual(evaluate(inventory)["decision"], visual.BLOCKED_DECISION)

    def test_existing_ui_without_sufficient_runtime_evidence_uses_generation(self):
        inventory = base_inventory("ui_copy")
        for domain in inventory.values():
            for value in domain:
                value["runtime_evidence_sufficient"] = False
        self.assertEqual(evaluate(inventory)["evidence_mode"], "generative_mockup")

    def test_new_and_redesigned_visuals_use_generation(self):
        for kind in (
            "new_screen",
            "new_component",
            "generated_web_asset",
            "redesign",
            "marketing_asset",
        ):
            with self.subTest(kind=kind):
                self.assertEqual(
                    evaluate(base_inventory(kind))["evidence_mode"], "generative_mockup"
                )

    def test_explicit_visual_request_uses_generation(self):
        inventory = base_inventory("backend")
        inventory["user_directions"][0]["directive"] = "request"
        self.assertEqual(evaluate(inventory)["evidence_mode"], "generative_mockup")


class DirectionTests(unittest.TestCase):
    def directions(self, older: str, newer: str, *, same_order=False):
        inventory = base_inventory("backend")
        inventory["user_directions"] = [
            entry(
                "UD-001",
                directive=older,
                authority="user",
                scope="D-001",
                source_order=1,
                turn="one",
            ),
            entry(
                "UD-002",
                directive=newer,
                authority="user",
                scope="D-001",
                source_order=1 if same_order else 2,
                turn="one" if same_order else "two",
            ),
        ]
        return inventory

    def test_newer_opt_out_supersedes_older_opt_in(self):
        self.assertEqual(evaluate(self.directions("request", "suppress"))["evidence_mode"], "none")

    def test_newer_opt_in_supersedes_older_opt_out(self):
        self.assertEqual(
            evaluate(self.directions("suppress", "request"))["evidence_mode"],
            "generative_mockup",
        )

    def test_same_turn_equal_authority_conflict_blocks(self):
        receipt = evaluate(self.directions("request", "suppress", same_order=True))
        self.assertEqual(receipt["decision"], visual.BLOCKED_DECISION)
        self.assertIsNone(receipt["evidence_mode"])

    def test_same_turn_conflicts_even_with_distinct_source_positions(self):
        inventory = self.directions("request", "suppress")
        inventory["user_directions"][1]["turn"] = "one"
        receipt = evaluate(inventory)
        self.assertEqual(receipt["decision"], visual.BLOCKED_DECISION)

    def test_higher_authority_wins_before_source_order(self):
        inventory = self.directions("request", "suppress")
        inventory["user_directions"][0]["authority"] = "system"
        receipt = evaluate(inventory)
        self.assertEqual(receipt["evidence_mode"], "generative_mockup")

    def test_opt_out_cannot_waive_visual_acceptance(self):
        inventory = base_inventory("nonvisual")
        inventory["user_directions"][0]["directive"] = "suppress"
        inventory["acceptance_criteria"][0]["kind"] = "new_screen"
        inventory["acceptance_criteria"][0]["source"] = "Acceptance requires a new screen"
        receipt = evaluate(inventory)
        self.assertEqual(receipt["decision"], visual.BLOCKED_DECISION)

    def test_no_imagegen_direction_allows_runtime_only_evidence(self):
        inventory = base_inventory("nonvisual")
        inventory["user_directions"][0]["directive"] = "suppress"
        inventory["acceptance_criteria"][0]["kind"] = "aria"
        inventory["acceptance_criteria"][0]["source"] = "Existing ARIA semantics"
        inventory["acceptance_criteria"][0]["runtime_evidence_sufficient"] = True
        receipt = evaluate(inventory)
        self.assertEqual(receipt["evidence_mode"], "runtime_capture")


class CompletenessAndAmbiguityTests(unittest.TestCase):
    def test_omitted_exact_set_entry_blocks_none(self):
        inventory = base_inventory()
        declared = declarations(inventory)
        declared["acceptance_criteria"] = ["AC-001", "AC-002"]
        receipt = evaluate(inventory, declared_ids=declared)
        self.assertEqual(receipt["decision"], visual.BLOCKED_DECISION)
        self.assertEqual(receipt["scope_inventory_status"], "incomplete")

    def test_duplicate_and_unclassified_entries_block_none(self):
        inventory = base_inventory()
        inventory["tasks"].append(copy.deepcopy(inventory["tasks"][0]))
        inventory["affected_modules"][0]["kind"] = "mystery"
        receipt = evaluate(inventory, declared_ids=declarations(inventory))
        self.assertEqual(receipt["decision"], visual.BLOCKED_DECISION)

    def test_masked_or_unknown_path_blocks(self):
        inventory = base_inventory()
        inventory["planned_paths"][0]["path"] = ""
        receipt = evaluate(inventory)
        self.assertEqual(receipt["decision"], visual.BLOCKED_DECISION)

    def test_material_ambiguity_blocks_but_nonmaterial_is_recorded(self):
        blocked = evaluate(base_inventory(), material_ambiguities=["could be a new screen"])
        self.assertEqual(blocked["decision"], visual.BLOCKED_DECISION)
        resolved = evaluate(
            base_inventory(),
            nonmaterial_uncertainties=["exact validator function name may change"],
        )
        self.assertEqual(resolved["evidence_mode"], "none")
        self.assertEqual(len(resolved["uncertainty"]), 1)


class ReplayAndTamperingTests(unittest.TestCase):
    def test_embedded_scope_is_recomputed_without_caller_copy(self):
        inventory = base_inventory()
        receipt = evaluate(inventory)
        receipt["evidence_mode"] = "runtime_capture"
        receipt["decision"] = "VISUAL_REQUIRED"
        _, _, errors = visual.validate_disposition(receipt, "authoritative body")
        self.assertTrue(any("does not match recomputation" in error for error in errors))

    def test_bootstrap_receipt_schema_remains_compatible(self):
        body = """# Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-001`

## Tasks
- [ ] **T-001 — Task.** Affected modules: `scripts/tool.py`. Requirements: complete.

## Acceptance Criteria
- WHEN invoked, THE SYSTEM SHALL pass. <!-- AC-001 -->
"""
        inventory, extraction_errors = visual.extract_scope_inventory(body)
        self.assertEqual(extraction_errors, [])
        receipt = {
            "policy_version": visual.POLICY_VERSION,
            "decision": "VISUAL_NOT_APPLICABLE",
            "evidence_mode": "none",
            "phase_binding": {
                "phase": "plan",
                "authoritative_issue_body_sha256": visual.canonical_sha256(body),
            },
            "scope_inventory_sha256": visual.inventory_sha256(inventory),
            "scope_inventory_status": "complete",
            "matched_triggers": [],
            "uncertainty": [],
            "evidence": ["explicit scope declaration"],
            "scoped_components": ["scripts/tool.py"],
        }
        _, _, errors = visual.validate_disposition(receipt, body)
        self.assertEqual(errors, [])
        _, _, v2_errors = visual.validate_disposition(
            receipt, body, require_embedded_inventory=True
        )
        self.assertTrue(any("embedded" in error for error in v2_errors))

    def test_phase_replay_is_rejected(self):
        inventory = base_inventory()
        receipt = evaluate(inventory, phase="plan")
        _, _, errors = visual.validate_disposition(
            receipt,
            "authoritative body",
            phase="implement-orientation",
            inventory=inventory,
            declared_ids=declarations(inventory),
        )
        self.assertTrue(any("phase binding is stale" in error for error in errors))

    def test_actual_frontend_diff_upgrades_planned_backend(self):
        planned = base_inventory("backend")
        receipt = evaluate(planned, phase="plan")
        actual = copy.deepcopy(planned)
        actual.pop("planned_paths")
        actual["actual_paths"] = [
            entry("P-001", "existing_component_state", path="web/Panel.tsx")
        ]
        review = evaluate(actual, phase="review")
        self.assertEqual(receipt["evidence_mode"], "none")
        self.assertEqual(review["evidence_mode"], "runtime_capture")

    def test_review_without_actual_diff_blocks(self):
        receipt = evaluate(base_inventory(), phase="review")
        self.assertEqual(receipt["decision"], visual.BLOCKED_DECISION)

    def test_review_receipt_must_match_independently_derived_diff(self):
        inventory = base_inventory("backend")
        inventory.pop("planned_paths")
        inventory["actual_paths"] = [
            entry("P-001", "backend", path="scripts/tool.py")
        ]
        receipt = evaluate(inventory, phase="review")
        _, _, errors = visual.validate_disposition(
            receipt,
            "authoritative body",
            phase="review",
            authoritative_paths=["web/NewScreen.tsx"],
        )
        self.assertTrue(any("independently derived" in error for error in errors))

    def test_scope_hash_tampering_is_rejected(self):
        inventory = base_inventory()
        receipt = evaluate(inventory)
        receipt["scope_inventory_sha256"] = "0" * 64
        _, _, errors = visual.validate_disposition(
            receipt,
            "authoritative body",
            inventory=inventory,
            declared_ids=declarations(inventory),
        )
        self.assertTrue(any("SHA-256" in error for error in errors))

    def test_forged_repository_signal_cannot_override_scope(self):
        inventory = base_inventory("backend")
        receipt = evaluate(
            inventory,
            repository_signals=[
                {
                    "path": "scripts/tool.py",
                    "signal": "frontend",
                    "provenance": "caller assertion",
                }
            ],
        )
        self.assertEqual(receipt["evidence_mode"], "none")

    def test_receipt_mode_forgery_is_rejected_by_recomputation(self):
        inventory = base_inventory("backend")
        receipt = evaluate(inventory)
        receipt["evidence_mode"] = "generative_mockup"
        receipt["decision"] = "VISUAL_REQUIRED"
        _, _, errors = visual.validate_disposition(
            receipt,
            "authoritative body",
            inventory=inventory,
            declared_ids=declarations(inventory),
        )
        self.assertTrue(any("does not match recomputation" in error for error in errors))

    def test_new_screen_path_cannot_be_declared_nonvisual(self):
        inventory = base_inventory("nonvisual")
        inventory["deliverables"][0]["source"] = "Add a new screen"
        inventory["affected_modules"][0]["source"] = "web/NewScreen.tsx"
        inventory["planned_paths"][0]["path"] = "web/NewScreen.tsx"
        receipt = evaluate(inventory)
        self.assertEqual(receipt["decision"], visual.BLOCKED_DECISION)
        self.assertTrue(
            any("source implies generative" in reason for reason in receipt["blocking_reasons"])
        )

    def test_receipt_cannot_substitute_sources_for_authoritative_new_screen(self):
        body = """# Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-001`

## Tasks
- [ ] **T-001 — Add new screen.** Affected modules: `web/NewScreen.tsx`. Requirements: render it.

## Acceptance Criteria
- WHEN opened, THE SYSTEM SHALL render the new screen. <!-- AC-001 -->
"""
        forged = base_inventory("backend")
        receipt = evaluate(forged, body=body)
        _, _, errors = visual.validate_disposition(receipt, body)
        self.assertTrue(
            any("authoritative issue text" in error for error in errors),
            errors,
        )

    def test_visual_deliverable_dominates_nonvisual_generator_path(self):
        body = """# Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-001`

## Problem Statement
Create and publish a marketing asset with a hero image.

## Tasks
- [ ] **T-001 — Create marketing asset.** Objective: publish the hero image. Affected modules: `scripts/generate_asset.py`. Requirements: produce it.

## Acceptance Criteria
- WHEN approved, THE SYSTEM SHALL publish the marketing asset. <!-- AC-001 -->
"""
        direction = "Create a visual marketing asset."
        inventory, errors = visual.build_plan_inventory(
            body, user_directions=[direction]
        )
        self.assertEqual(errors, [])
        receipt = visual.evaluate_visual_applicability(
            inventory,
            phase="plan",
            authoritative_issue_body=body,
            declared_ids=declarations(inventory),
        )
        self.assertEqual(receipt["evidence_mode"], "generative_mockup")
        _, _, validation_errors = visual.validate_disposition(
            receipt,
            body,
            require_embedded_inventory=True,
            authoritative_user_directions=[direction],
        )
        self.assertEqual(validation_errors, [])

    def test_authoritative_deliverable_kind_cannot_be_forged_nonvisual(self):
        body = """# Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-001`

## Problem Statement
Create and publish a marketing asset with a hero image.

## Tasks
- [ ] **T-001 — Create marketing asset.** Objective: publish the hero image. Context: produce a visual deliverable. Affected modules: `scripts/generate_asset.py`. Requirements: produce it. Verification: inspect it. Complete when approved. Owner lane: design. `depends_on: []`.

## Acceptance Criteria
- WHEN approved, THE SYSTEM SHALL publish the marketing asset. <!-- AC-001 -->
"""
        direction = "Create a visual marketing asset."
        inventory, errors = visual.build_plan_inventory(
            body, user_directions=[direction]
        )
        self.assertEqual(errors, [])
        forged = copy.deepcopy(inventory)
        forged["deliverables"][0]["kind"] = "nonvisual"
        receipt = visual.evaluate_visual_applicability(
            forged,
            phase="plan",
            authoritative_issue_body=body,
            declared_ids=declarations(forged),
        )
        _, _, validation_errors = visual.validate_disposition(
            receipt,
            body,
            require_embedded_inventory=True,
            authoritative_user_directions=[direction],
        )
        self.assertTrue(
            any("deliverables" in error for error in validation_errors),
            validation_errors,
        )

    def test_build_plan_inventory_uses_canonical_multiline_task_grammar(self):
        body = """# Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-001`

## Problem Statement
Update a validator workflow.

## Tasks
- [ ] **T-001 — Update validator.**
  Objective: update validation.
  Context: preserve current behavior.
  Affected modules: `scripts/tool.py`.
  Requirements: pass deterministically.
  Verification: run focused tests.
  Complete when all checks pass.
  Owner lane: core.
  `depends_on: []`.

## Acceptance Criteria
- WHEN invoked, THE SYSTEM SHALL pass. <!-- AC-001 -->
"""
        inventory, errors = visual.build_plan_inventory(
            body, user_directions=["Do not generate images for backend work."]
        )
        self.assertEqual(errors, [])
        self.assertEqual([task["id"] for task in inventory["tasks"]], ["T-001"])
        self.assertEqual(
            [module["source"] for module in inventory["affected_modules"]],
            ["scripts/tool.py"],
        )

    def test_runtime_planned_paths_retain_sufficiency_decision(self):
        body = """# Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-001`

## Problem Statement
Adjust an existing component state.

## Tasks
- [ ] **T-001 — Adjust existing component.** Objective: update state behavior. Context: existing UI. Affected modules: `web/Panel.tsx`. Requirements: preserve layout. Verification: inspect the runtime. Complete when verified. Owner lane: web. `depends_on: []`.

## Acceptance Criteria
- WHEN opened, THE SYSTEM SHALL expose the existing component state. <!-- AC-001 -->
"""
        inventory, errors = visual.build_plan_inventory(
            body,
            user_directions=["Use current runtime evidence."],
            runtime_evidence=[
                {
                    "kind": "screenshot",
                    "evidence": "Current Panel state",
                    "scope_ids": ["D-001", "T-001", "M-001", "AC-001"],
                    "artifact_sha256": "a" * 64,
                    "captured_at": "2026-07-29T12:00:00Z",
                }
            ],
            runtime_evidence_not_before="2026-07-29T11:00:00Z",
        )
        self.assertEqual(errors, [])
        self.assertTrue(inventory["planned_paths"][0]["runtime_evidence_sufficient"])
        receipt = visual.evaluate_visual_applicability(
            inventory,
            phase="plan",
            authoritative_issue_body=body,
            declared_ids=declarations(inventory),
        )
        self.assertEqual(receipt["evidence_mode"], "runtime_capture")

    def test_runtime_sufficiency_defaults_false_without_bound_current_evidence(self):
        body = """# Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-001`

## Problem Statement
Adjust an existing component state.

## Tasks
- [ ] **T-001 — Adjust existing component.** Objective: update state behavior. Context: existing UI. Affected modules: `web/Panel.tsx`. Requirements: preserve layout. Verification: inspect the runtime. Complete when verified. Owner lane: web. `depends_on: []`.

## Acceptance Criteria
- WHEN opened, THE SYSTEM SHALL expose the existing component state. <!-- AC-001 -->
"""
        inventory, errors = visual.build_plan_inventory(
            body, user_directions=["Use current runtime evidence."]
        )
        self.assertEqual(errors, [])
        self.assertFalse(
            inventory["planned_paths"][0]["runtime_evidence_sufficient"]
        )
        receipt = visual.evaluate_visual_applicability(
            inventory,
            phase="plan",
            authoritative_issue_body=body,
            declared_ids=declarations(inventory),
        )
        self.assertEqual(receipt["evidence_mode"], "generative_mockup")

    def test_policy_terminology_cannot_mask_visual_deliverable(self):
        body = """# Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-001`

## Problem Statement
Create a marketing asset and hero image while recording visual-applicability.

## Tasks
- [ ] **T-001 — Create hero image.** Objective: create a marketing asset. Context: visual-applicability. Affected modules: `web/hero-image.png`. Requirements: produce the hero image. Verification: inspect it. Complete when approved. Owner lane: web. `depends_on: []`.

## Acceptance Criteria
- WHEN complete, THE SYSTEM SHALL provide the hero image and record visual-applicability. <!-- AC-001 -->
"""
        inventory, errors = visual.build_plan_inventory(
            body, user_directions=["Generate the requested visual."]
        )
        self.assertEqual(errors, [])
        receipt = visual.evaluate_visual_applicability(
            inventory,
            phase="plan",
            authoritative_issue_body=body,
            declared_ids=declarations(inventory),
        )
        self.assertEqual(receipt["evidence_mode"], "generative_mockup")

    def test_user_direction_directive_cannot_be_forged(self):
        body = """# Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-001`

## Problem Statement
Update a validator workflow.

## Tasks
- [ ] **T-001 — Update validator.** Affected modules: `scripts/tool.py`. Requirements: pass.

## Acceptance Criteria
- WHEN invoked, THE SYSTEM SHALL pass. <!-- AC-001 -->
"""
        direction = "Use ImageGen for visual exploration."
        inventory, errors = visual.build_plan_inventory(
            body, user_directions=[direction]
        )
        self.assertEqual(errors, [])
        mutations = {
            "id": "UD-999",
            "kind": "backend",
            "source": direction + " altered",
            "source_sha256": "0" * 64,
            "provenance": "caller assertion",
            "directive": "neutral",
            "authority": "repository",
            "scope": "D-999",
            "source_order": 99,
            "turn": "forged",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                forged = copy.deepcopy(inventory)
                forged["user_directions"][0][field] = value
                receipt = visual.evaluate_visual_applicability(
                    forged,
                    phase="plan",
                    authoritative_issue_body=body,
                    declared_ids=declarations(forged),
                )
                _, _, validation_errors = visual.validate_disposition(
                    receipt,
                    body,
                    require_embedded_inventory=True,
                    authoritative_user_directions=[direction],
                )
                self.assertTrue(
                    any(
                        "direction semantics" in error
                        for error in validation_errors
                    ),
                    validation_errors,
                )


if __name__ == "__main__":
    unittest.main()
