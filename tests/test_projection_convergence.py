from __future__ import annotations

import copy
import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import graph_transaction
import plan_audits
import plan_graph
import plan_phase_validation
import plan_tasks
import preflight_plan
import projection_bundle
import projection_kernel
import visual_policy
from plan_protocol import PlanProtocolError, issue_body_sha256


FIXTURE = ROOT / "tests" / "fixtures" / "projection_convergence" / "golden_plan.md"
AUTHORITY_URL = "https://github.com/mknoth197/evidence-gated-delivery/issues/24"
VERSIONS = {
    "kernel": "projection-kernel/v1",
    "reader": "github-issue-reader/v1",
    "canonicalizer": "github-issue-body/v1",
}
POLICIES = {
    "assurance": "assurance-policy/v1",
    "visual": "visual-applicability/v1",
    "graph": "graph-policy/v1",
}
DIRECTIONS = ["Implement the nonvisual validator workflow without ImageGen."]


def audit(body: str) -> dict:
    receipt = {
        "audit_id": "audit-final",
        "agent_id": str(uuid.UUID("11111111-1111-4111-8111-111111111111")),
        "agent_path": "/root/independent_plan_spec_auditor_final",
        "role_marker": "Independent Plan spec auditor",
        "kind": "final_remote",
        "reviewed_body_sha256": issue_body_sha256(body),
        "started_at": "2026-08-04T12:00:00Z",
        "completed_at": "2026-08-04T12:01:00Z",
        "evidence_ids": ["golden-plan"],
        "findings": [],
        "predecessor_audit_id": None,
        "predecessor_finding_ids": [],
    }
    receipt["result_sha256"] = plan_audits._audit_result_hash(receipt)
    return receipt


def prepare(authority_bytes: bytes | None = None):
    raw = FIXTURE.read_bytes() if authority_bytes is None else authority_bytes
    text = raw.decode("utf-8")
    adapters = {
        "tasks": plan_tasks.task_projection_adapter,
        "graph_policy": plan_tasks.graph_policy_projection_adapter(
            evaluated_at="2026-08-04T12:00:00Z"
        ),
        "graph_draft": plan_graph.graph_draft_projection_adapter(
            parent_issue_url=AUTHORITY_URL,
            repository="mknoth197/evidence-gated-delivery",
        ),
        "visual_disposition": visual_policy.visual_disposition_projection_adapter(
            phase="plan", user_directions=DIRECTIONS
        ),
        "plan_audit_inputs": plan_audits.plan_audit_inputs_projection_adapter(
            audits=[audit(text)]
        ),
        "preflight": preflight_plan.preflight_projection_adapter(
            user_directions=DIRECTIONS
        ),
    }
    return projection_kernel.run_projection_kernel(
        raw,
        authority={
            "kind": "github_issue",
            "locator": AUTHORITY_URL,
            "source_revision": issue_body_sha256(text),
        },
        versions=VERSIONS,
        policy_versions=POLICIES,
        assurance={
            "requested": "heavy",
            "effective": "heavy",
            "selection_origin": "legacy_phase_command",
            "legacy_subprofile": "deep",
        },
        capsule_generation={
            "capsule_id": "plan-24",
            "generation": 1,
            "digest": "a" * 64,
        },
        adapters=adapters,
        required_slots=list(adapters),
        prepared_at="2026-08-04T12:02:00Z",
        completed_at="2026-08-04T12:03:00Z",
        intent={
            "risk_classification": "ordinary_scoped_recoverable",
            "authority_ref": "plan-24",
            "staged_action_digest": "b" * 64,
        },
    )


def manifest_data(body: str) -> dict:
    return {
        "implementation_issue_url": AUTHORITY_URL,
        "run_started_at": "2026-08-04T11:00:00Z",
        "phase_timeline": {"plan_completed_at": "2026-08-04T12:05:00Z"},
        "effective_assurance": "heavy",
        "requested_assurance": "heavy",
        "selection_origin": "legacy_phase_command",
        "legacy_subprofile": "deep",
        "context_capsule_ref": {
            "schema_version": "context-capsule/v1",
            "capsule_id": "plan-24",
            "generation": 1,
            "digest": "a" * 64,
            "locator": "/tmp/plan-24-capsule.json",
        },
        "visual_user_directions": DIRECTIONS,
        "runtime_visual_evidence": [],
        "plan_audits": [audit(body)],
        "graph_policy_receipt": {
            "evaluated_at": "2026-08-04T12:00:00Z"
        },
    }


class ProjectionConvergenceTests(unittest.TestCase):
    def test_cutover_helper_assembles_and_replays_all_six_consumers(self):
        raw = FIXTURE.read_bytes()
        data = manifest_data(raw.decode("utf-8"))
        evidence = plan_phase_validation.assemble_plan_projection_transaction_evidence(
            data,
            raw,
            prepared_at="2026-08-04T12:02:00Z",
            completed_at="2026-08-04T12:03:00Z",
        )
        data["projection_transaction_evidence_required"] = True
        data["projection_transaction_evidence"] = evidence
        self.assertEqual(
            plan_phase_validation.validate_projection_transaction_evidence(data, raw),
            [],
        )
        self.assertEqual(
            evidence["required_slots"],
            list(plan_phase_validation.PLAN_PROJECTION_REQUIRED_SLOTS),
        )

    def test_cutover_flag_requires_evidence_but_legacy_manifest_does_not(self):
        self.assertEqual(
            plan_phase_validation.validate_projection_transaction_evidence({}), []
        )
        self.assertEqual(
            plan_phase_validation.validate_projection_transaction_evidence(
                {"projection_transaction_evidence_required": False}
            ),
            [],
        )
        errors = plan_phase_validation.validate_projection_transaction_evidence(
            {"projection_transaction_evidence_required": True}, FIXTURE.read_bytes()
        )
        self.assertIn("projection_transaction_evidence is required", errors)

    def test_cutover_rejects_current_body_drift_and_semantic_slot_forgery(self):
        raw = FIXTURE.read_bytes()
        data = manifest_data(raw.decode("utf-8"))
        evidence = plan_phase_validation.assemble_plan_projection_transaction_evidence(
            data,
            raw,
            prepared_at="2026-08-04T12:02:00Z",
            completed_at="2026-08-04T12:03:00Z",
        )
        data["projection_transaction_evidence_required"] = True
        data["projection_transaction_evidence"] = evidence
        newline_errors = plan_phase_validation.validate_projection_transaction_evidence(
            data, raw + b"\n"
        )
        self.assertTrue(any("bytes_digest conflict" in error for error in newline_errors))

        forged = copy.deepcopy(evidence)
        tasks_payload = forged["bundle"]["slots"]["tasks"]["payload"]
        tasks_payload["tasks"][0]["title"] = "Forged but self-consistent"
        forged["bundle"]["slots"]["tasks"]["payload_digest"] = (
            projection_bundle.projection_sha256(tasks_payload)
        )
        forged["bundle"]["prepared_digest"] = projection_bundle.prepared_bundle_digest(
            forged["bundle"]
        )
        forged["bundle"]["bundle_id"] = projection_bundle.bundle_id_for_digest(
            forged["bundle"]["prepared_digest"]
        )
        forged["receipt"] = projection_bundle.build_projection_transaction_receipt(
            forged["bundle"],
            intent=evidence["receipt"]["intent"],
            completed_at=evidence["receipt"]["completed_at"],
            required_slots=evidence["required_slots"],
        )
        data["projection_transaction_evidence"] = forged
        forgery_errors = plan_phase_validation.validate_projection_transaction_evidence(
            data, raw
        )
        self.assertTrue(
            any("projection tasks differ" in error for error in forgery_errors),
            forgery_errors,
        )

    def test_golden_projections_share_one_digest_and_closed_versions(self):
        envelope = prepare()
        self.assertEqual(envelope["receipt"]["final_state"], "sealed")
        bundle = envelope["bundle"]
        digest = bundle["authority"]["bytes_digest"]
        self.assertTrue(bundle["slots"])
        for name, slot in bundle["slots"].items():
            with self.subTest(name=name):
                self.assertEqual(slot["state"], "present")
                self.assertEqual(slot["payload"]["input_digest"], digest)
                self.assertEqual(
                    slot["payload"]["adapter_version"], slot["projection_version"]
                )
        self.assertEqual(len(plan_tasks.tasks_from_projection_bundle(bundle)), 1)
        self.assertEqual(
            plan_tasks.graph_policy_from_projection_bundle(bundle)["disposition"],
            "NO_GRAPH",
        )
        self.assertEqual(
            visual_policy.visual_disposition_from_projection_bundle(bundle)[
                "evidence_mode"
            ],
            "none",
        )
        self.assertEqual(
            plan_audits.validate_plan_audits_from_projection_bundle(bundle), []
        )
        self.assertEqual(
            preflight_plan.preflight_from_projection_bundle(bundle)["status"], "VALID"
        )

    def test_transport_newline_mutation_changes_prepared_identity(self):
        original = prepare()["bundle"]
        mutated = prepare(FIXTURE.read_bytes() + b"\n")["bundle"]
        self.assertNotEqual(original["authority"]["bytes_digest"], mutated["authority"]["bytes_digest"])
        self.assertNotEqual(original["prepared_digest"], mutated["prepared_digest"])

    def test_unknown_slot_vocabulary_fails_closed_per_projection(self):
        raw = FIXTURE.read_bytes()
        result = projection_kernel.run_projection_kernel(
            raw,
            authority={"kind": "github_issue", "locator": AUTHORITY_URL, "source_revision": "x"},
            versions=VERSIONS,
            policy_versions=POLICIES,
            assurance={"requested": "heavy", "effective": "heavy", "selection_origin": "test", "legacy_subprofile": None},
            capsule_generation={"capsule_id": "plan-24", "generation": 1, "digest": "a" * 64},
            adapters={"tasks": plan_tasks.task_projection_adapter, "invented": plan_tasks.task_projection_adapter},
            required_slots=["tasks"],
            prepared_at="2026-08-04T12:02:00Z",
            completed_at="2026-08-04T12:03:00Z",
            intent={"risk_classification": "ordinary_scoped_recoverable", "authority_ref": "plan-24", "staged_action_digest": "b" * 64},
        )
        self.assertEqual(result["receipt"]["final_state"], "blocked")
        blockers = result["receipt"]["blockers"]
        self.assertTrue(any(item.get("projection") == "invented" for item in blockers))

    def test_changed_projection_or_input_digest_is_rejected(self):
        bundle = prepare()["bundle"]
        changed = copy.deepcopy(bundle)
        changed["slots"]["tasks"]["payload"]["tasks"][0]["title"] = "Changed"
        with self.assertRaisesRegex(PlanProtocolError, "invalid projection bundle"):
            plan_tasks.tasks_from_projection_bundle(changed)

        rebound = copy.deepcopy(bundle)
        payload = rebound["slots"]["tasks"]["payload"]
        payload["input_digest"] = "0" * 64
        rebound["slots"]["tasks"]["payload_digest"] = projection_bundle.projection_sha256(payload)
        rebound["prepared_digest"] = projection_bundle.prepared_bundle_digest(rebound)
        rebound["bundle_id"] = projection_bundle.bundle_id_for_digest(rebound["prepared_digest"])
        with self.assertRaisesRegex(PlanProtocolError, "does not share"):
            plan_tasks.tasks_from_projection_bundle(rebound)

    def test_staged_intent_survives_crash_before_readback_without_identity_mutation(self):
        bundle = prepare()["bundle"]
        draft = plan_graph.graph_draft_from_projection_bundle(bundle)
        remote = {"children": [], "edges": []}
        staged = graph_transaction.stage_graph_external_intent(
            bundle, draft, remote, target=AUTHORITY_URL
        )
        before = (bundle["bundle_id"], bundle["prepared_digest"])
        records: list[dict] = []
        with self.assertRaisesRegex(PlanProtocolError, "prewrite readback raised"):
            graph_transaction.execute_transaction(
                draft,
                remote,
                guard=lambda: [],
                runner=lambda _action: {"ok": True},
                readback=lambda: (_ for _ in ()).throw(RuntimeError("interrupted")),
                recorder=records.append,
                projection_bundle=bundle,
                staged_intent=staged,
            )
        self.assertEqual(before, (bundle["bundle_id"], bundle["prepared_digest"]))
        self.assertEqual(records[-1]["result"]["reason"], "prewrite_readback_exception")

    def test_remote_mismatch_is_bound_to_prepared_identity_and_rejected(self):
        bundle = prepare()["bundle"]
        draft = plan_graph.graph_draft_from_projection_bundle(bundle)
        mismatch = {"children": [{"task_id": "T-999"}], "edges": []}
        evidence = plan_graph.normalize_graph_readback_evidence(bundle, draft, mismatch)
        self.assertEqual(evidence["prepared_digest"], bundle["prepared_digest"])
        self.assertEqual(evidence["reconciliation"]["classification"], "CONFLICT")
        tampered = copy.deepcopy(evidence)
        tampered["prepared_digest"] = "0" * 64
        with self.assertRaisesRegex(PlanProtocolError, "prepared_digest mismatch"):
            plan_graph.validate_graph_readback_evidence(tampered, bundle)

        forged = copy.deepcopy(evidence)
        forged["reconciliation"] = {"classification": "EXACT_MATCH", "reasons": []}
        with self.assertRaisesRegex(PlanProtocolError, "reconciliation mismatch"):
            plan_graph.validate_graph_readback_evidence(forged, bundle, draft)

    def test_encrypted_auditor_proof_remains_exactly_body_bound(self):
        bundle = prepare()["bundle"]
        payload = bundle["slots"]["plan_audit_inputs"]["payload"]
        self.assertEqual(payload["audits"][0]["agent_path"], "/root/independent_plan_spec_auditor_final")
        self.assertEqual(plan_audits.validate_plan_audits_from_projection_bundle(bundle), [])
        replay = copy.deepcopy(bundle)
        replay_payload = replay["slots"]["plan_audit_inputs"]["payload"]
        replay_payload["audits"][0]["reviewed_body_sha256"] = "0" * 64
        replay["slots"]["plan_audit_inputs"]["payload_digest"] = projection_bundle.projection_sha256(replay_payload)
        replay["prepared_digest"] = projection_bundle.prepared_bundle_digest(replay)
        replay["bundle_id"] = projection_bundle.bundle_id_for_digest(replay["prepared_digest"])
        errors = plan_audits.validate_plan_audits_from_projection_bundle(replay)
        self.assertTrue(any("exact canonical remote" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
