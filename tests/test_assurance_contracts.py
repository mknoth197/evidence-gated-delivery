from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAPSULE = (ROOT / "references" / "context-capsule-v1.md").read_text(encoding="utf-8")
BUNDLE = (ROOT / "references" / "projection-bundle-v1.md").read_text(encoding="utf-8")
PHASES = (ROOT / "references" / "phase-contracts.md").read_text(encoding="utf-8")
MANIFEST = (ROOT / "references" / "run-manifest.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


def json_examples(document: str) -> list[dict]:
    return [json.loads(body) for body in re.findall(r"```json\n(.*?)\n```", document, re.DOTALL)]


def section(document: str, heading: str) -> str:
    match = re.search(rf"^##+ {re.escape(heading)}\n(.*?)(?=^##+ |\Z)", document, re.MULTILINE | re.DOTALL)
    if not match:
        raise AssertionError(f"missing section: {heading}")
    return match.group(1)


def compact(document: str) -> str:
    return " ".join(document.split())


class AssuranceContractTests(unittest.TestCase):
    """Executable documentation contract for Plan #24 T-001."""

    def test_ac_001_assurance_grammar_and_legacy_mapping_are_closed(self):
        self.assertIn("[--assurance light|heavy]", BUNDLE)
        for field in (
            "mode",
            "requested_assurance",
            "requested_legacy_tier",
            "effective_assurance",
            "legacy_subprofile",
            "selection_origin",
            "achieved_assurance",
        ):
            self.assertIn(f"`{field}`", BUNDLE)
        mappings = {
            "Legacy phase command without selector": ("heavy", "legacy_phase_command"),
            "Legacy inferred/freeform command": ("heavy", "legacy_inferred_command"),
            "Legacy tier `quick`": ("light", "legacy_tier"),
            "Legacy tier `balanced`": ("light", "legacy_tier"),
            "Legacy tier `deep`": ("heavy", "legacy_tier"),
        }
        for source, (effective, origin) in mappings.items():
            row = next(line for line in BUNDLE.splitlines() if source in line)
            self.assertIn(f"`{effective}`", row)
            self.assertIn(f"`{origin}`", row)
        self.assertIn("`status` rejects it", BUNDLE)
        self.assertIn("BLOCKED_ASSURANCE_SELECTION", BUNDLE)

    def test_ac_002_and_ac_008_capsule_fields_lifecycle_and_cas_are_frozen(self):
        invariant_fields = {
            "objective",
            "settled_decisions",
            "source_revisions",
            "evidence_refs",
            "execution_frontier",
            "unresolved_questions",
            "next_action",
        }
        example = json_examples(CAPSULE)[0]
        self.assertEqual(example["schema_version"], "context-capsule/v1")
        self.assertTrue(invariant_fields <= example.keys())
        self.assertRegex(example["digest"], r"^[0-9a-f]{64}$")
        for operation in ("create", "checkpoint", "resume", "fork", "compact", "supersede", "archive"):
            self.assertIn(f"`{operation}`", CAPSULE)
        for invariant in ("expected_generation", "expected_digest", "BLOCKED_CAPSULE_CONFLICT", "BLOCKED_SOURCE_DRIFT"):
            self.assertIn(f"`{invariant}`", CAPSULE)
        self.assertIn("atomic replace", CAPSULE)

    def test_ac_003_light_corridor_is_bounded_and_omissions_are_explicit(self):
        expected = {
            "inspect",
            "edit",
            "test",
            "repair",
            "branch",
            "commit",
            "publish_review_branch",
            "open_pull_request",
            "update_scoped_issue",
            "publish_deterministic_graph",
            "verify_remote_readback",
        }
        corridor = section(BUNDLE, "Light progress corridor")
        declared = set(re.findall(r"`([a-z_]+)`", corridor.split("Light never", 1)[0]))
        self.assertEqual(declared, expected)
        self.assertIn("policy-justified omissions", corridor)
        for forbidden in ("design tournament", "multiple-auditor gate", "phase receipt", "remote provider activation"):
            self.assertIn(forbidden, compact(corridor))

    def test_ac_004_exactly_six_hard_boundaries_fail_closed(self):
        hard_boundaries = section(BUNDLE, "Exactly six hard boundaries")
        rows = re.findall(r"^\| `([a-z_]+)` \|", hard_boundaries, re.MULTILINE)
        self.assertEqual(
            rows,
            [
                "protected_external_write",
                "destructive_or_irreversible",
                "production_or_release",
                "sensitive_data_access",
                "missing_authority",
                "material_architecture_ambiguity",
            ],
        )
        self.assertIn("Unknown or contradictory classification fails closed", hard_boundaries)
        for outcome in ("authorize", "narrow", "stop"):
            self.assertIn(f"`{outcome}`", hard_boundaries)
        for evidence in ("rule_id", "added Heavy controls", "estimated incremental latency/cost"):
            self.assertIn(evidence, compact(hard_boundaries))

    def test_ac_005_heavy_preserves_deep_gates(self):
        for gate in (
            "manifest",
            "audit",
            "tournament",
            "Plan Protocol v2",
            "visual",
            "graph",
            "provider-evidence",
            "external-action verification",
            "retrospective",
            "receipt",
        ):
            self.assertIn(gate, BUNDLE)
        self.assertIn("No legacy invocation silently downgrades", compact(BUNDLE))

    def test_prepared_bundle_and_transaction_receipt_have_separate_identity(self):
        prepared, receipt = json_examples(BUNDLE)
        self.assertEqual(prepared["schema_version"], "projection-bundle/v1")
        self.assertEqual(receipt["schema_version"], "projection-transaction-receipt/v1")
        self.assertEqual(receipt["bundle_id"], prepared["bundle_id"])
        self.assertEqual(receipt["prepared_digest"], prepared["prepared_digest"])
        self.assertNotIn("final_state", prepared)
        self.assertEqual(receipt["final_state"], "blocked")
        self.assertEqual(receipt["external_actions"][0]["state"], "started")
        for state in ("present", "omitted", "pending", "blocked"):
            self.assertIn(f"`{state}`", BUNDLE)
        self.assertIn("never mutate the prepared identity", BUNDLE)

    def test_ac_012_migration_is_honest_and_rollback_is_lossless(self):
        for document in (CAPSULE, BUNDLE):
            self.assertIn("`legacy-envelope/v1`", document)
            self.assertIn("`transactional_completeness: unproven`", document)
            self.assertIn("original", document.lower())
            self.assertIn("rollback", document.lower())
        self.assertIn("never relabelled `sealed`", BUNDLE)
        self.assertIn("without downgrading Heavy, deleting capsules, or rewriting bundles/receipts", compact(BUNDLE))

    def test_ac_013_distribution_drift_is_read_only(self):
        drift = section(BUNDLE, "Migration, distribution drift, and rollback")
        for state in ("IN_SYNC", "DRIFT", "UNKNOWN"):
            self.assertIn(f"`{state}`", drift)
        self.assertIn("read-only", drift)
        self.assertIn("must not write, synchronize, or overwrite", compact(drift))

    def test_contracts_are_cross_linked_from_operator_and_phase_docs(self):
        for document in (README, PHASES, MANIFEST):
            self.assertIn("context-capsule-v1.md", document)
            self.assertIn("projection-bundle-v1.md", document)

    def test_privacy_and_external_action_boundaries_are_explicit(self):
        for forbidden in ("credentials", "tokens", "private prompts", "hidden reasoning"):
            self.assertIn(forbidden, compact(BUNDLE))
        for unauthorized in ("merge", "deployment", "release", "provider activation", "credential/account mutation"):
            self.assertIn(unauthorized, compact(BUNDLE))
        self.assertIn("mutation receipt and remote read-back", compact(BUNDLE))


if __name__ == "__main__":
    unittest.main()
