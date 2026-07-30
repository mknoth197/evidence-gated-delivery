from __future__ import annotations

import copy
import hashlib
import importlib.util
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "visual_applicability", ROOT / "scripts" / "visual_applicability.py"
)
assert SPEC and SPEC.loader
visual = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(visual)


def png_bytes(
    scanlines: bytes = b"\x00\x00\x00\x00\xff",
    *,
    color_type: int = 6,
    extra_chunks: tuple[tuple[bytes, bytes], ...] = (),
) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            chunk(
                b"IHDR",
                struct.pack(">IIBBBBB", 1, 1, 8, color_type, 0, 0, 0),
            ),
            *(chunk(kind, payload) for kind, payload in extra_chunks),
            chunk(b"IDAT", zlib.compress(scanlines)),
            chunk(b"IEND", b""),
        )
    )


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

class RuntimeEvidenceAndIntentTests(unittest.TestCase):
    def test_runtime_evidence_requires_readable_matching_bytes_and_aware_time(self):
        base = {
            "kind": "screenshot",
            "evidence": "Current Panel state",
            "scope_ids": ["T-001"],
            "artifact_sha256": "a" * 64,
            "captured_at": "2026-07-29T12:00:00Z",
        }
        self.assertFalse(
            visual.runtime_evidence_sufficient("T-001", [base])
        )
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "panel.png"
            artifact.write_bytes(png_bytes())
            valid = dict(
                base,
                artifact_path=str(artifact),
                artifact_sha256=hashlib.sha256(
                    artifact.read_bytes()
                ).hexdigest(),
            )
            self.assertTrue(
                visual.runtime_evidence_sufficient(
                    "T-001",
                    [valid],
                    not_after="2026-07-29T12:05:00Z",
                )
            )
            wrong_type = Path(directory) / "hosts.png"
            wrong_type.write_bytes(b"127.0.0.1 localhost\n")
            forged_type = dict(
                valid,
                artifact_path=str(wrong_type),
                artifact_sha256=hashlib.sha256(
                    wrong_type.read_bytes()
                ).hexdigest(),
            )
            self.assertFalse(
                visual.runtime_evidence_sufficient(
                    "T-001",
                    [forged_type],
                    not_after="2026-07-29T12:05:00Z",
                )
            )
            header_only = Path(directory) / "header-only.png"
            header_only.write_bytes(b"\x89PNG\r\n\x1a\nTHIS IS NOT A PNG")
            forged_png = dict(
                valid,
                artifact_path=str(header_only),
                artifact_sha256=hashlib.sha256(
                    header_only.read_bytes()
                ).hexdigest(),
            )
            self.assertFalse(
                visual.runtime_evidence_sufficient(
                    "T-001",
                    [forged_png],
                    not_after="2026-07-29T12:05:00Z",
                )
            )
            bomb = Path(directory) / "bomb.png"
            bomb.write_bytes(png_bytes(b"\x00" * 1_000_000))
            forged_bomb = dict(
                valid,
                artifact_path=str(bomb),
                artifact_sha256=hashlib.sha256(bomb.read_bytes()).hexdigest(),
            )
            self.assertFalse(
                visual.runtime_evidence_sufficient(
                    "T-001",
                    [forged_bomb],
                    not_after="2026-07-29T12:05:00Z",
                )
            )
            for name, content in (
                (
                    "indexed.png",
                    png_bytes(
                        b"\x00\x00",
                        color_type=3,
                        extra_chunks=((b"PLTE", b"\x00\x00\x00"),),
                    ),
                ),
                (
                    "critical.png",
                    png_bytes(extra_chunks=((b"ABCD", b""),)),
                ),
                (
                    "bad-chunk-name.png",
                    png_bytes(extra_chunks=((b"ab1d", b""),)),
                ),
                (
                    "transparency-before-palette.png",
                    png_bytes(
                        b"\x00\x00",
                        color_type=3,
                        extra_chunks=(
                            (b"tRNS", b"\xff"),
                            (b"PLTE", b"\x00\x00\x00"),
                        ),
                    ),
                ),
                (
                    "duplicate-transparency.png",
                    png_bytes(
                        b"\x00\x00",
                        color_type=3,
                        extra_chunks=(
                            (b"PLTE", b"\x00\x00\x00"),
                            (b"tRNS", b"\xff"),
                            (b"tRNS", b"\xff"),
                        ),
                    ),
                ),
                (
                    "transparency-before-optional-palette.png",
                    png_bytes(
                        b"\x00\x00\x00\x00",
                        color_type=2,
                        extra_chunks=(
                            (b"tRNS", b"\x00" * 6),
                            (b"PLTE", b"\x00\x00\x00"),
                        ),
                    ),
                ),
                (
                    "invalid-text-keyword.png",
                    png_bytes(
                        extra_chunks=((b"tEXt", b" bad  key\0text"),)
                    ),
                ),
                (
                    "inconsistent-gamma.png",
                    png_bytes(
                        extra_chunks=(
                            (b"sRGB", b"\x00"),
                            (b"gAMA", struct.pack(">I", 1)),
                        )
                    ),
                ),
                (
                    "inconsistent-chromaticity.png",
                    png_bytes(
                        extra_chunks=(
                            (b"sRGB", b"\x00"),
                            (b"cHRM", b"\x00" * 32),
                        )
                    ),
                ),
            ):
                invalid_png = Path(directory) / name
                invalid_png.write_bytes(content)
                invalid_evidence = dict(
                    valid,
                    artifact_path=str(invalid_png),
                    artifact_sha256=hashlib.sha256(content).hexdigest(),
                )
                if name == "indexed.png":
                    self.assertTrue(
                        visual.runtime_evidence_sufficient(
                            "T-001",
                            [invalid_evidence],
                            not_after="2026-07-29T12:05:00Z",
                        )
                    )
                else:
                    self.assertFalse(
                        visual.runtime_evidence_sufficient(
                            "T-001",
                            [invalid_evidence],
                            not_after="2026-07-29T12:05:00Z",
                        )
                    )
            naive = dict(valid, captured_at="2026-07-29T12:00:00")
            self.assertFalse(
                visual.runtime_evidence_sufficient(
                    "T-001",
                    [naive],
                    not_before="2026-07-29T11:00:00Z",
                    not_after="2026-07-29T12:05:00Z",
                )
            )
            future = dict(valid, captured_at="2099-01-01T00:00:00Z")
            self.assertFalse(
                visual.runtime_evidence_sufficient(
                    "T-001",
                    [future],
                    not_after="2026-07-29T12:05:00Z",
                )
            )
            boundary = dict(valid, captured_at="2026-07-29T12:10:00Z")
            self.assertTrue(
                visual.runtime_evidence_sufficient(
                    "T-001",
                    [boundary],
                    not_after="2026-07-29T12:05:00Z",
                )
            )
            beyond_boundary = dict(
                valid, captured_at="2026-07-29T12:10:01Z"
            )
            self.assertFalse(
                visual.runtime_evidence_sufficient(
                    "T-001",
                    [beyond_boundary],
                    not_after="2026-07-29T12:05:00Z",
                )
            )
            self.assertFalse(
                visual.runtime_evidence_sufficient(
                    "T-001",
                    [valid],
                    not_before="2026-07-29T11:00:00",
                    not_after="2026-07-29T12:05:00Z",
                )
            )

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

    def test_broader_visual_deliverables_cannot_fall_through_to_nonvisual(self):
        for deliverable, path in (
            ("product illustration", "assets/product-illustration.svg"),
            ("icon set", "assets/product-icons.svg"),
            ("company logo", "scripts/generate_asset.py"),
            ("cover artwork", "scripts/generate_asset.py"),
            ("product photograph", "scripts/generate_asset.py"),
            ("infographic", "scripts/generate_asset.py"),
            ("technical diagram", "scripts/generate_asset.py"),
            ("emoji pack", "scripts/generate_asset.py"),
            ("HTML email template", "scripts/generate_asset.py"),
            ("presentation template", "scripts/generate_asset.py"),
            ("printed certificate template", "scripts/generate_asset.py"),
            ("event invitation template", "scripts/generate_asset.py"),
        ):
            with self.subTest(deliverable=deliverable):
                body = f"""# Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-002`

## Problem Statement
Create a {deliverable} while recording visual-applicability.

## Tasks
- [ ] **T-001 — Create {deliverable}.** Objective: create a {deliverable}. Context: visual-applicability. Affected modules: `{path}`, `scripts/generate_illustration.py`. Requirements: produce the {deliverable}. Verification: inspect it. Complete when approved. Owner lane: design. `depends_on: []`.

## Acceptance Criteria
- WHEN complete, THE SYSTEM SHALL provide the {deliverable} and record visual-applicability. <!-- AC-001 -->
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
                self.assertEqual(
                    receipt["evidence_mode"], "generative_mockup"
                )

    def test_unknown_created_deliverable_blocks_instead_of_defaulting_none(self):
        for deliverable in (
            "launch badge for API documentation",
            "commemorative medallion for the service launch",
            "printed certificate for a workflow milestone",
            "API launch badge",
            "workflow milestone certificate",
            "service award medallion",
            "documentation commemorative plaque",
            "launch badge for API documentation",
            "commemorative plaque",
            "award medallion",
            "webinar slide template",
            "mood board",
        ):
            for verb in (
                "Create",
                "Build",
                "Make",
                "Craft",
                "Develop",
                "Draft",
                "Assemble",
            ):
                with self.subTest(deliverable=deliverable, verb=verb):
                    body = f"""# Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-001`

## Problem Statement
{verb} a {deliverable}.

## Tasks
- [ ] **T-001 — {verb} {deliverable}.** Objective: {verb.lower()} the requested deliverable. Context: launch. Affected modules: `scripts/generate_asset.py`. Requirements: produce it. Verification: inspect it. Complete when approved. Owner lane: design. `depends_on: []`.

## Acceptance Criteria
- WHEN complete, THE SYSTEM SHALL provide the requested deliverable. <!-- AC-001 -->
"""
                    inventory, errors = visual.build_plan_inventory(
                        body,
                        user_directions=[
                            "Use the workflow's visual policy."
                        ],
                    )
                    self.assertEqual(errors, [])
                    receipt = visual.evaluate_visual_applicability(
                        inventory,
                        phase="plan",
                        authoritative_issue_body=body,
                        declared_ids=declarations(inventory),
                    )
                    self.assertEqual(
                        receipt["decision"], visual.BLOCKED_DECISION
                    )
                    self.assertIsNone(receipt["evidence_mode"])

    def test_complete_bound_scope_resolves_unknown_nonvisual_goal(self):
        for goal in (
            "Improve build performance",
            "Reduce CI latency",
            "Harden authentication",
        ):
            with self.subTest(goal=goal):
                body = f"""# Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-001`

## Problem Statement
{goal}.

## Tasks
- [ ] **T-001 — {goal}.** Objective: improve the validator workflow. Context: automation. Affected modules: `scripts/tool.py`. Requirements: preserve behavior. Verification: run tests. Complete when verified. Owner lane: core. `depends_on: []`.

## Acceptance Criteria
- WHEN invoked, THE SYSTEM SHALL pass validation. <!-- AC-001 -->
"""
                inventory, errors = visual.build_plan_inventory(
                    body,
                    user_directions=[
                        "Do not generate images for this nonvisual workflow."
                    ],
                )
                self.assertEqual(errors, [])
                receipt = visual.evaluate_visual_applicability(
                    inventory,
                    phase="plan",
                    authoritative_issue_body=body,
                    declared_ids=declarations(inventory),
                )
                self.assertEqual(receipt["evidence_mode"], "none")

    def test_ambiguous_acceptance_criterion_cannot_become_nonvisual(self):
        for verb in (
            "provide",
            "generate",
            "produce",
            "display",
            "show",
            "create",
            "render",
            "export",
            "return",
            "present",
            "expose",
        ):
            with self.subTest(verb=verb):
                body = f"""# Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-001`

## Problem Statement
Update a validator workflow.

## Tasks
- [ ] **T-001 — Update validator.** Objective: update validation. Context: automation. Affected modules: `scripts/tool.py`. Requirements: preserve behavior. Verification: run tests. Complete when verified. Owner lane: core. `depends_on: []`.

## Acceptance Criteria
- WHEN complete, THE SYSTEM SHALL {verb} a launch badge. <!-- AC-001 -->
"""
                inventory, errors = visual.build_plan_inventory(
                    body, user_directions=["Use the visual policy."]
                )
                self.assertEqual(errors, [])
                self.assertEqual(
                    inventory["acceptance_criteria"][0]["kind"],
                    "ambiguous_visual_intent",
                )
                receipt = visual.evaluate_visual_applicability(
                    inventory,
                    phase="plan",
                    authoritative_issue_body=body,
                    declared_ids=declarations(inventory),
                )
                self.assertEqual(receipt["decision"], visual.BLOCKED_DECISION)

    def test_generic_output_head_cannot_prove_nonvisual_scope(self):
        for deliverable in (
            "launch badge output",
            "visual output",
            "launch badge result",
        ):
            with self.subTest(deliverable=deliverable):
                body = f"""# Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-001`

## Problem Statement
Create {deliverable}.

## Tasks
- [ ] **T-001 — Create {deliverable}.** Objective: create the requested artifact. Context: launch. Affected modules: `scripts/generate_asset.py`. Requirements: produce it. Verification: inspect it. Complete when approved. Owner lane: design. `depends_on: []`.

## Acceptance Criteria
- WHEN complete, THE SYSTEM SHALL render {deliverable}. <!-- AC-001 -->
"""
                inventory, errors = visual.build_plan_inventory(
                    body, user_directions=["Use the visual policy."]
                )
                self.assertEqual(errors, [])
                receipt = visual.evaluate_visual_applicability(
                    inventory,
                    phase="plan",
                    authoritative_issue_body=body,
                    declared_ids=declarations(inventory),
                )
                self.assertEqual(receipt["decision"], visual.BLOCKED_DECISION)

    def test_nonvisual_result_phrases_require_safe_governing_verbs(self):
        for verb, deliverable, expected in (
            ("return", "validation results", "VISUAL_NOT_APPLICABLE"),
            ("display", "test results", visual.BLOCKED_DECISION),
            ("show", "API responses", visual.BLOCKED_DECISION),
            ("render", "command outputs", visual.BLOCKED_DECISION),
        ):
            with self.subTest(verb=verb, deliverable=deliverable):
                body = f"""# Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-001`

## Problem Statement
Validate a backend workflow.

## Tasks
- [ ] **T-001 — Validate workflow.** Objective: validate it. Context: backend. Affected modules: `scripts/validate.py`. Requirements: return a result. Verification: run tests. Complete when green. Owner lane: workflow. `depends_on: []`.

## Acceptance Criteria
- WHEN complete, THE SYSTEM SHALL {verb} {deliverable}. <!-- AC-001 -->
"""
                inventory, errors = visual.build_plan_inventory(
                    body, user_directions=["Use the visual policy."]
                )
                self.assertEqual(errors, [])
                receipt = visual.evaluate_visual_applicability(
                    inventory,
                    phase="plan",
                    authoritative_issue_body=body,
                    declared_ids=declarations(inventory),
                )
                self.assertEqual(receipt["decision"], expected)

    def test_article_free_artifact_actions_remain_ambiguous(self):
        for goal in (
            "Draft mood board",
            "Develop webinar slide templates",
            "Assemble launch badges",
        ):
            with self.subTest(goal=goal):
                body = f"""# Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-001`

## Problem Statement
{goal}.

## Tasks
- [ ] **T-001 — {goal}.** Objective: produce the requested output. Context: launch. Affected modules: `scripts/generate_asset.py`. Requirements: complete it. Verification: inspect it. Complete when approved. Owner lane: design. `depends_on: []`.

## Acceptance Criteria
- WHEN complete, THE SYSTEM SHALL expose the requested output. <!-- AC-001 -->
"""
                inventory, errors = visual.build_plan_inventory(
                    body, user_directions=["Use the visual policy."]
                )
                self.assertEqual(errors, [])
                receipt = visual.evaluate_visual_applicability(
                    inventory,
                    phase="plan",
                    authoritative_issue_body=body,
                    declared_ids=declarations(inventory),
                )
                self.assertEqual(receipt["decision"], visual.BLOCKED_DECISION)

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
