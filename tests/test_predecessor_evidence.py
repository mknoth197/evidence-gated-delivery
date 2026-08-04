from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from scripts.phase_validation_orchestrator import PhaseValidationDependencies, validate
from scripts.predecessor_evidence import (
    _copied_evidence_errors,
    validate_predecessor_evidence,
)
from scripts.review_phase_validation import plan_with_successor_visual_evidence


BODY_SHA = "a" * 64
WORKFLOW_VERSION = "evidence-gated-delivery/plan-protocol-v2"
PROTOCOL_VERSION = "plan-protocol/v2"
RESEARCH_URL = "https://github.com/example/evidence-gated-delivery/issues/22"
PLAN_URL = "https://github.com/example/evidence-gated-delivery/issues/24"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PredecessorEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.manifest_path = self.root / "plan-manifest.json"
        self.receipt_path = self.root / "plan-receipt.json"
        self.predecessor = self._manifest("original-parent")
        self.predecessor["run_id"] = "plan-run"
        self.predecessor["predecessor_evidence"] = {}
        self.predecessor["research_evidence"] = {"source": ["authenticated"]}
        self.predecessor["plan_audits"] = [{"audit_id": "plan-audit"}]
        self._write_manifest()
        self.receipt = {
            "status": "VALID",
            "phase": "plan",
            "run_id": "plan-run",
            "manifest": str(self.manifest_path),
            "manifest_sha256": sha256(self.manifest_path),
            "validated_at": "2026-08-03T17:09:03Z",
            "remote_verification": True,
            "errors": [],
        }
        self._write_receipt()
        self.successor = self._manifest("successor-parent")
        self.successor["run_id"] = "implement-run"
        self.successor["predecessor_evidence"] = self._binding()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def _manifest(parent_thread_id: str) -> dict:
        return {
            "run_id": "run",
            "parent_thread_id": parent_thread_id,
            "workflow_version": WORKFLOW_VERSION,
            "plan_protocol_version": PROTOCOL_VERSION,
            "research_issue_url": RESEARCH_URL,
            "implementation_issue_url": PLAN_URL,
            "initiative_identity": {
                "name": "Evidence projection",
                "slug": "evidence-projection",
                "research_issue_url": RESEARCH_URL,
                "implementation_issue_url": PLAN_URL,
            },
            "visual_artifact_disposition": {
                "phase_binding": {"authoritative_issue_body_sha256": BODY_SHA}
            },
            "research_evidence": {},
            "plan_audits": [],
            "plan_events": [{"type": "protocol_initialized", "event_sha256": parent_thread_id}],
            "contestants": [],
            "judges": [],
            "contestant_images": [],
            "judge_rubric": [],
            "semantic_visual_reviews": [],
            "graph_policy_receipt": {},
            "graph_capability_receipt": {},
            "graph_draft": {},
            "graph_actions": [],
            "graph_remote_state": {},
            "phase_receipt_bindings": {},
            "phase_retrospectives": [],
            "phase_transition_judgments": [],
            "automation_decisions": [],
        }

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(json.dumps(self.predecessor, indent=2) + "\n")

    def _write_receipt(self) -> None:
        self.receipt_path.write_text(json.dumps(self.receipt, indent=2) + "\n")

    def _binding(self) -> dict:
        return {
            "phase": "plan",
            "receipt_path": str(self.receipt_path),
            "receipt_sha256": sha256(self.receipt_path),
            "manifest_path": str(self.manifest_path),
            "manifest_sha256": sha256(self.manifest_path),
            "predecessor_parent_thread_id": "original-parent",
            "workflow_version": WORKFLOW_VERSION,
            "plan_protocol_version": PROTOCOL_VERSION,
            "validated_at": "2026-08-03T17:09:03Z",
            "authority": {
                "repository": "example/evidence-gated-delivery",
                "initiative_slug": "evidence-projection",
                "research_issue_url": RESEARCH_URL,
                "implementation_issue_url": PLAN_URL,
                "implementation_issue_body_sha256": BODY_SHA,
            },
        }

    @staticmethod
    def _live_authority(_: str) -> str:
        return BODY_SHA

    def _validate(self, **kwargs):
        kwargs.setdefault(
            "trusted_receipt_sha256s",
            {self.successor["predecessor_evidence"]["receipt_sha256"]},
        )
        return validate_predecessor_evidence(
            self.successor,
            "plan",
            live_authority_sha256=self._live_authority,
            **kwargs,
        )

    @staticmethod
    def _orchestrator_dependencies() -> PhaseValidationDependencies:
        return PhaseValidationDependencies(
            nonempty=lambda value: isinstance(value, str) and bool(value.strip()),
            github_readback=Mock(),
            validate_timeline=Mock(),
            validate_trace_audit=Mock(),
            add_research_errors=Mock(),
            add_plan_errors=Mock(),
            add_orientation_errors=Mock(),
            add_implement_errors=Mock(),
            review_changed_paths=Mock(return_value=[]),
            validate_retrospective_gate=Mock(),
            validate_transition_gate=Mock(),
            add_handoff_error=Mock(),
            mode_by_phase={
                "research": {"research"},
                "plan": {"plan"},
                "orchestrate-preapproval": {"orchestrate"},
                "implement": {"implement"},
                "review": {"review"},
            },
        )

    @staticmethod
    def _phase_identity(manifest: dict, mode: str) -> None:
        manifest.update(
            {
                "mode": mode,
                "goal": "validate isolated phase",
                "selected_mode_reason": "test",
                "phase_timeline": {},
            }
        )

    def test_loads_exact_valid_predecessor_with_normalized_immutable_result(self) -> None:
        result = self._validate()

        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.binding["phase"], "plan")
        self.assertEqual(result.binding["status"], "VALID")
        self.assertEqual(result.binding["receipt_sha256"], sha256(self.receipt_path))
        self.assertEqual(result.typed_binding.phase, "plan")
        self.assertEqual(result.manifest["parent_thread_id"], "original-parent")
        self.assertEqual(result.receipt["remote_verification"], True)
        with self.assertRaises(TypeError):
            result.manifest["run_id"] = "changed"

    def test_implement_orchestrator_projects_plan_without_copying_fields(self) -> None:
        self._phase_identity(self.successor, "implement")
        self.successor["phase_transition_judgments"] = [{
            "phase": "plan",
            "phase_receipt_sha256": sha256(self.receipt_path),
        }]
        deps = self._orchestrator_dependencies()

        errors = validate(self.successor, "implement", True, deps=deps)

        self.assertEqual(errors, [])
        predecessor_plan = deps.add_implement_errors.call_args.kwargs["predecessor_plan"]
        self.assertEqual(predecessor_plan["parent_thread_id"], "original-parent")
        transition = deps.validate_transition_gate.call_args
        self.assertEqual(
            transition.kwargs["predecessor_binding"]["receipt_sha256"],
            sha256(self.receipt_path),
        )
        self.assertNotIn("contestants", self.successor["predecessor_evidence"])

    def test_successor_visual_projection_retains_plan_provenance(self) -> None:
        predecessor = copy.deepcopy(self.predecessor)
        predecessor["contestants"] = [{"agent_id": "plan-agent"}]
        for phase in ("implement", "review"):
            with self.subTest(phase=phase):
                successor = {
                    "visual_artifact_disposition": {
                        "phase_binding": {
                            "phase": phase,
                            "authoritative_issue_body_sha256": BODY_SHA,
                        }
                    },
                    "phase_timeline": {f"{phase}_completed_at": "2026-08-04T12:00:00Z"},
                    "run_started_at": "2026-08-04T10:00:00Z",
                }
                projected = plan_with_successor_visual_evidence(
                    predecessor, successor, phase
                )
                self.assertEqual(projected["parent_thread_id"], "original-parent")
                self.assertEqual(projected["contestants"], predecessor["contestants"])
                self.assertIs(
                    projected["visual_artifact_disposition"],
                    successor["visual_artifact_disposition"],
                )
                self.assertEqual(projected["phase_timeline"], successor["phase_timeline"])

    def test_compact_nonvisual_implement_derives_authenticated_inventory(self) -> None:
        predecessor = copy.deepcopy(self.predecessor)
        predecessor["visual_artifact_disposition"].update(
            status="resolved",
            decision="VISUAL_NOT_APPLICABLE",
            evidence_mode="none",
            scope_inventory={"deliverables": [{"id": "D-001"}]},
        )
        successor = {
            "visual_artifact_disposition": {
                "status": "resolved",
                "decision": "VISUAL_NOT_APPLICABLE",
                "evidence_mode": "none",
                "phase_binding": {
                    "phase": "implement",
                    "authoritative_issue_body_sha256": BODY_SHA,
                },
            }
        }
        projected = plan_with_successor_visual_evidence(
            predecessor, successor, "implement"
        )
        disposition = projected["visual_artifact_disposition"]
        self.assertEqual(disposition["phase_binding"]["phase"], "implement")
        self.assertEqual(disposition["scope_inventory"], {"deliverables": [{"id": "D-001"}]})
        self.assertEqual(
            predecessor["visual_artifact_disposition"]["phase_binding"].get("phase"),
            None,
        )

    def test_review_orchestrator_resolves_implement_then_plan_chain(self) -> None:
        implement_path = self.root / "implement-manifest.json"
        implement_receipt_path = self.root / "implement-receipt.json"
        implement = self._manifest("implement-parent")
        implement["run_id"] = "implement-run"
        implement["predecessor_evidence"] = self._binding()
        implement["phase_transition_judgments"] = [{
            "phase": "plan",
            "phase_receipt_sha256": sha256(self.receipt_path),
        }]
        implement["implementation_workers"] = [{"agent_id": "fresh-worker"}]
        implement_path.write_text(json.dumps(implement, indent=2) + "\n")
        implement_receipt = {
            "status": "VALID",
            "phase": "implement",
            "run_id": "implement-run",
            "manifest": str(implement_path),
            "manifest_sha256": sha256(implement_path),
            "validated_at": "2026-08-04T12:00:00Z",
            "remote_verification": True,
            "errors": [],
        }
        implement_receipt_path.write_text(json.dumps(implement_receipt, indent=2) + "\n")
        review = self._manifest("review-parent")
        review["run_id"] = "review-run"
        self._phase_identity(review, "review")
        review["review_dispositions_recorded"] = True
        review["remote_checks_reported"] = True
        review["predecessor_evidence"] = {
            **self._binding(),
            "phase": "implement",
            "receipt_path": str(implement_receipt_path),
            "receipt_sha256": sha256(implement_receipt_path),
            "manifest_path": str(implement_path),
            "manifest_sha256": sha256(implement_path),
            "predecessor_parent_thread_id": "implement-parent",
            "validated_at": "2026-08-04T12:00:00Z",
        }
        review["phase_transition_judgments"] = [{
            "phase": "implement",
            "phase_receipt_sha256": sha256(implement_receipt_path),
        }]
        review["quality_gates"] = [{
            "name": "review tests",
            "status": "passed",
            "evidence": "fresh Review rerun",
        }]
        deps = self._orchestrator_dependencies()

        errors = validate(review, "review", True, deps=deps)

        self.assertEqual(errors, [])
        implement_call = deps.add_implement_errors.call_args
        self.assertEqual(implement_call.args[0]["run_id"], "implement-run")
        self.assertEqual(
            implement_call.kwargs["predecessor_plan"]["run_id"], "plan-run"
        )

    def test_review_allows_fresh_evidence_but_rejects_exact_implement_copies(self) -> None:
        implement = {
            "acceptance_reviewer": {"agent_id": "implement-acceptance"},
            "quality_gates": [{"name": "tests", "status": "passed", "evidence": "old"}],
        }
        review = {
            "acceptance_reviewer": {"agent_id": "review-acceptance"},
            "quality_gates": [{"name": "tests", "status": "passed", "evidence": "fresh"}],
        }

        self.assertEqual(_copied_evidence_errors(review, implement, "implement"), [])

        review["acceptance_reviewer"] = copy.deepcopy(
            implement["acceptance_reviewer"]
        )
        review["quality_gates"] = copy.deepcopy(implement["quality_gates"])
        errors = _copied_evidence_errors(review, implement, "implement")

        self.assertTrue(any("acceptance_reviewer" in error for error in errors), errors)
        self.assertTrue(any("quality_gates" in error for error in errors), errors)

        review["acceptance_reviewer"] = {
            "agent_id": "implement-acceptance",
            "result": "changed callback",
        }
        review["quality_gates"] = [{
            "name": "tests",
            "status": "passed",
            "evidence": "fresh",
        }]
        errors = _copied_evidence_errors(review, implement, "implement")
        self.assertTrue(
            any("reuses the Implement acceptance reviewer agent" in error for error in errors),
            errors,
        )

    def test_rejects_tampered_receipt_bytes(self) -> None:
        self.receipt["validated_at"] = "2026-08-04T00:00:00Z"
        self._write_receipt()

        result = self._validate()

        self.assertFalse(result.valid)
        self.assertTrue(any("receipt SHA-256" in error for error in result.errors), result.errors)

    def test_rejects_rehashed_receipt_without_original_authenticated_anchor(self) -> None:
        original_sha = self.successor["predecessor_evidence"]["receipt_sha256"]
        self.receipt["validated_at"] = "2026-08-04T00:00:00Z"
        self._write_receipt()
        self.successor["predecessor_evidence"]["receipt_sha256"] = sha256(self.receipt_path)
        self.successor["predecessor_evidence"]["validated_at"] = self.receipt["validated_at"]

        result = validate_predecessor_evidence(
            self.successor,
            "plan",
            live_authority_sha256=self._live_authority,
            trusted_receipt_sha256s={original_sha},
        )

        self.assertFalse(result.valid)
        self.assertTrue(any("authenticated transition anchor" in error for error in result.errors))

    def test_rejects_tampered_manifest_bytes(self) -> None:
        self.predecessor["goal"] = "tampered"
        self._write_manifest()

        result = self._validate()

        self.assertFalse(result.valid)
        self.assertTrue(any("manifest SHA-256" in error for error in result.errors), result.errors)

    def test_rejects_parent_id_substitution(self) -> None:
        self.successor["predecessor_evidence"]["predecessor_parent_thread_id"] = "successor-parent"

        result = self._validate()

        self.assertFalse(result.valid)
        self.assertTrue(any("original predecessor provenance" in error for error in result.errors))

    def test_rejects_cross_workstream_or_repository_rebinding(self) -> None:
        other_url = "https://github.com/other/repository/issues/24"
        self.successor["implementation_issue_url"] = other_url
        self.successor["initiative_identity"]["implementation_issue_url"] = other_url

        result = self._validate()

        self.assertFalse(result.valid)
        self.assertTrue(any("successor" in error and "authority" in error for error in result.errors))

    def test_rejects_stale_live_authority(self) -> None:
        result = validate_predecessor_evidence(
            self.successor,
            "plan",
            live_authority_sha256=lambda _: "b" * 64,
            trusted_receipt_sha256s={
                self.successor["predecessor_evidence"]["receipt_sha256"]
            },
        )

        self.assertFalse(result.valid)
        self.assertTrue(any("stale or mismatched" in error for error in result.errors))

    def test_rejects_cycle_from_ancestor_receipt(self) -> None:
        result = self._validate(
            ancestor_receipt_sha256s={self.successor["predecessor_evidence"]["receipt_sha256"]}
        )

        self.assertFalse(result.valid)
        self.assertTrue(any("cycle detected" in error for error in result.errors))

    def test_rejects_manual_copy_of_predecessor_only_evidence(self) -> None:
        self.successor["research_evidence"] = copy.deepcopy(self.predecessor["research_evidence"])

        result = self._validate()

        self.assertFalse(result.valid)
        self.assertTrue(any("predecessor-only evidence" in error for error in result.errors))

    def test_rejects_modified_manual_copy_and_allows_current_transition(self) -> None:
        self.successor["research_evidence"] = {"source": ["modified copy"]}
        self.successor["phase_retrospectives"] = [{"phase": "plan"}]

        result = self._validate()

        self.assertFalse(result.valid)
        self.assertTrue(any("research_evidence" in error for error in result.errors))
        self.assertFalse(any("phase_retrospectives" in error for error in result.errors))

    def test_rejects_earlier_phase_transition_evidence(self) -> None:
        self.successor["phase_retrospectives"] = [{"phase": "research"}]

        result = self._validate()

        self.assertFalse(result.valid)
        self.assertTrue(any("earlier-phase evidence" in error for error in result.errors))

    def test_rejects_receipt_that_is_not_remotely_verified_and_clean(self) -> None:
        self.receipt["remote_verification"] = False
        self.receipt["errors"] = ["failure"]
        self._write_receipt()
        self.successor["predecessor_evidence"]["receipt_sha256"] = sha256(self.receipt_path)

        result = self._validate()

        self.assertFalse(result.valid)
        self.assertTrue(any("remote_verification true" in error for error in result.errors))
        self.assertTrue(any("empty array" in error for error in result.errors))

    def test_rejects_binding_with_unknown_fields(self) -> None:
        self.successor["predecessor_evidence"]["manual_parent_override"] = "successor-parent"

        result = self._validate()

        self.assertFalse(result.valid)
        self.assertTrue(any("exactly the frozen binding fields" in error for error in result.errors))

    def test_rejects_relative_paths(self) -> None:
        self.successor["predecessor_evidence"]["receipt_path"] = "plan-receipt.json"

        result = self._validate()

        self.assertFalse(result.valid)
        self.assertTrue(any("must be an absolute path" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
