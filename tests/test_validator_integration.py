from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import plan_protocol
from scripts import validate_run as validator


class CollaborationAuditIntegrationTests(unittest.TestCase):
    parent_id = "019f0000-0000-7000-8000-000000000001"
    agent_id = "019f0000-0000-7000-8000-000000000094"
    agent_path = "/root/plan_audit"
    result = "PASS plan evidence-id-1"
    intermediary_id = "019f0000-0000-7000-8000-000000000093"

    def write_sessions(
        self,
        root: Path,
        callback: str | None = None,
        *,
        task_name: str = "plan_execution_audit",
        message: str = "Execution auditor phase: plan\nAudit the evidence.",
    ) -> None:
        directory = root / "sessions" / "2026" / "07" / "29"
        directory.mkdir(parents=True)
        child = [
            {
                "type": "session_meta",
                "payload": {
                    "id": self.agent_id,
                    "timestamp": "2026-07-29T12:05:00Z",
                    "thread_source": "subagent",
                    "source": {
                        "subagent": {
                            "thread_spawn": {
                                "parent_thread_id": self.parent_id,
                                "depth": 1,
                                "agent_path": self.agent_path,
                            }
                        }
                    },
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-07-29T12:10:00Z",
                "payload": {
                    "type": "task_complete",
                    "last_agent_message": self.result,
                },
            },
        ]
        (directory / f"rollout-{self.agent_id}.jsonl").write_text(
            "\n".join(json.dumps(item) for item in child) + "\n"
        )
        call_id = "call-collaboration-audit"
        parent = [
            {
                "type": "session_meta",
                "payload": {
                    "id": self.parent_id,
                    "timestamp": "2026-07-29T12:00:00Z",
                    "thread_source": "user",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "namespace": "collaboration",
                    "name": "spawn_agent",
                    "call_id": call_id,
                    "arguments": json.dumps(
                        {
                            "task_name": task_name,
                            "message": message,
                        }
                    ),
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-07-29T12:05:00Z",
                "payload": {
                    "type": "sub_agent_activity",
                    "event_id": call_id,
                    "agent_thread_id": self.agent_id,
                    "agent_path": self.agent_path,
                    "kind": "started",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "agent_message",
                    "author": self.agent_path,
                    "recipient": "/root",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Payload:\n{callback or self.result}",
                        }
                    ],
                },
            },
        ]
        (directory / f"rollout-{self.parent_id}.jsonl").write_text(
            "\n".join(json.dumps(item) for item in parent) + "\n"
        )

    def audit(self, marker: str = "Execution auditor phase: plan") -> dict[str, object]:
        return {
            "phase": "plan",
            "receipt_kind": "collaboration_delegated",
            "agent_id": self.agent_id,
            "agent_path": self.agent_path,
            "role_marker": marker,
            "status": "completed",
            "verdict": "PASS",
            "result": self.result,
            "result_sha256": hashlib.sha256(self.result.encode()).hexdigest(),
            "verified_event_ids": ["evidence-id-1"],
        }

    def validate(self, audit: dict[str, object]) -> list[str]:
        errors: list[str] = []
        validator.validate_trace_audit(
            {
                "run_started_at": "2026-07-29T12:00:00Z",
                "phase_timeline": {"plan_completed_at": "2026-07-29T12:30:00Z"},
                "parent_thread_id": self.parent_id,
                "trace_audits": [audit],
            },
            "plan",
            errors,
        )
        return errors

    def test_accepts_parent_child_authenticated_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.write_sessions(Path(temporary))
            with patch.dict(os.environ, {"CODEX_HOME": temporary}):
                self.assertEqual(self.validate(self.audit()), [])

    def test_rejects_marker_and_callback_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.write_sessions(Path(temporary), callback="PASS plan tampered")
            with patch.dict(os.environ, {"CODEX_HOME": temporary}):
                errors = self.validate(self.audit("Execution auditor phase: research"))
        self.assertTrue(any("callback does not match" in error for error in errors))

    def test_accepts_encrypted_message_when_task_name_persists_role(self):
        self.assertTrue(
            validator.persisted_delegation_role_matches(
                json.dumps(
                    {
                        "task_name": "execution_auditor_phase_research",
                        "message": "gAAAAABencrypted",
                    }
                ),
                "Execution auditor phase: research",
            )
        )
        self.assertTrue(
            validator.persisted_delegation_role_matches(
                {
                    "task_name": "execution_auditor_phase_research_2",
                    "message": "gAAAAABencrypted",
                },
                "Execution auditor phase: research",
            )
        )
        self.assertFalse(
            validator.persisted_delegation_role_matches(
                json.dumps(
                    {"task_name": "generic_review", "message": "gAAAAABencrypted"}
                ),
                "Execution auditor phase: research",
            )
        )
        for ambiguous in (
            "implementation_research_audit",
            "research_plan_auditor",
            "execution_auditor_phase_research_plan",
            "execution_auditor_phase_research_0",
            "execution_auditor_phase_research_recheck",
        ):
            with self.subTest(ambiguous=ambiguous):
                self.assertFalse(
                    validator.persisted_delegation_role_matches(
                        {"task_name": ambiguous, "message": "gAAAAABencrypted"},
                        "Execution auditor phase: research",
                    )
                )
        self.assertTrue(
            validator.persisted_delegation_role_matches(
                {
                    "task_name": "independent_plan_spec_auditor_final",
                    "message": "gAAAAABencrypted",
                },
                "Independent Plan spec auditor",
            )
        )
        for forbidden in (
            "plan_execution_audit",
            "plan_audit",
            "phase_plan_auditor",
            "plan_transition_audit",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    validator.persisted_delegation_role_matches(
                        {"task_name": forbidden, "message": "gAAAAABencrypted"},
                        "Independent Plan spec auditor",
                    )
                )

    def test_encrypted_transition_judge_uses_exact_task_name(self):
        expected = "phase transition judge: plan -> implement"
        self.assertTrue(
            validator.persisted_delegation_role_matches(
                {
                    "task_name": "phase_transition_judge_plan_to_implement",
                    "message": "gAAAAABencrypted",
                },
                expected,
            )
        )
        self.assertTrue(
            validator.persisted_delegation_role_matches(
                {
                    "task_name": "phase_transition_judge_plan_to_implement_2",
                    "message": "gAAAAABencrypted",
                },
                expected,
            )
        )
        self.assertFalse(
            validator.persisted_delegation_role_matches(
                {
                    "task_name": "plan_transition_judge",
                    "message": "gAAAAABencrypted",
                },
                expected,
            )
        )
        for invalid in (
            "phase_transition_judge_plan_to_implement_0",
            "phase_transition_judge_plan_to_implement_recheck",
            "phase_transition_judge_implement_to_review_2",
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(
                    validator.persisted_delegation_role_matches(
                        {"task_name": invalid, "message": "gAAAAABencrypted"},
                        expected,
                    )
                )

    def reviewer(self) -> dict[str, object]:
        return {
            "receipt_kind": "collaboration_delegated",
            "agent_id": self.agent_id,
            "agent_path": self.agent_path,
            "status": "completed",
            "result": self.result,
            "result_sha256": hashlib.sha256(self.result.encode()).hexdigest(),
            "started_at": "2026-07-29T12:05:00Z",
            "completed_at": "2026-07-29T12:10:00Z",
        }

    def test_authenticates_encrypted_test_coverage_reviewer(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.write_sessions(
                Path(temporary),
                task_name="test_coverage_reviewer",
                message="gAAAAABencrypted",
            )
            errors: list[str] = []
            with patch.dict(os.environ, {"CODEX_HOME": temporary}):
                reviewer_id = validator.validate_reviewer(
                    {"parent_thread_id": self.parent_id},
                    self.reviewer(),
                    "test_reviewer",
                    errors,
                    expected_marker="Test-Coverage Reviewer",
                )
        self.assertEqual(reviewer_id, self.agent_id)
        self.assertEqual(errors, [])

    def test_rejects_transition_marker_for_test_coverage_reviewer(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.write_sessions(
                Path(temporary),
                task_name="phase_transition_judge_implement_to_review",
                message="gAAAAABencrypted",
            )
            errors: list[str] = []
            with patch.dict(os.environ, {"CODEX_HOME": temporary}):
                validator.validate_reviewer(
                    {"parent_thread_id": self.parent_id},
                    self.reviewer(),
                    "test_reviewer",
                    errors,
                    expected_marker="Test-Coverage Reviewer",
                )
        self.assertTrue(any("required role marker" in error for error in errors))

    def test_plaintext_role_text_cannot_replace_exact_coverage_task_name(self):
        self.assertFalse(
            validator.persisted_delegation_role_matches(
                {
                    "task_name": "generic_review",
                    "message": "Act as Test-Coverage Reviewer",
                },
                "Test-Coverage Reviewer",
            )
        )

    def test_rejects_unauthenticated_test_coverage_receipt(self):
        errors: list[str] = []
        receipt = self.reviewer()
        receipt.pop("receipt_kind")
        validator.validate_reviewer(
            {"parent_thread_id": self.parent_id},
            receipt,
            "test_reviewer",
            errors,
            expected_marker="Test-Coverage Reviewer",
        )
        self.assertTrue(any("collaboration_delegated" in error for error in errors))


class PlanProtocolValidatorIntegrationTests(unittest.TestCase):
    body = """# Plan

## Tasks

- [ ] **T-001 — Validate plan.** Objective: validate. Context: protocol. Affected modules: `scripts/validate_run.py`. Requirements: fail closed. Verification: unit tests. Complete when: tests pass. Owner lane: core. `depends_on: []`.

## Out of Scope

No UI.
"""

    def setUp(self):
        self._temporary_codex_home = tempfile.TemporaryDirectory()
        self._codex_home_patch = patch.dict(
            os.environ, {"CODEX_HOME": self._temporary_codex_home.name}
        )
        self._codex_home_patch.start()

    def tearDown(self):
        self._codex_home_patch.stop()
        self._temporary_codex_home.cleanup()

    def manifest(self) -> dict[str, object]:
        body_sha = plan_protocol.issue_body_sha256(self.body)
        tasks = plan_protocol.parse_tasks(self.body)
        policy = plan_protocol.evaluate_graph_policy(
            tasks, evaluated_at="2026-07-29T12:10:00Z"
        )
        audit = {
            "audit_id": "audit-final",
            "agent_id": "019f0000-0000-7000-8000-000000000095",
            "agent_path": "/root/final_plan_audit",
            "receipt_kind": "collaboration_delegated",
            "status": "completed",
            "role_marker": "Independent Plan spec auditor",
            "kind": "final_remote",
            "reviewed_body_sha256": body_sha,
            "started_at": "2026-07-29T12:05:00Z",
            "completed_at": "2026-07-29T12:06:00Z",
            "evidence_ids": ["remote-body"],
            "findings": [],
            "predecessor_audit_id": None,
            "predecessor_finding_ids": [],
        }
        callback = (
            "PASS remote-body\n"
            + plan_protocol.plan_audit_callback_marker(audit)
        )
        audit["callback_sha256"] = hashlib.sha256(callback.encode()).hexdigest()
        audit["result_sha256"] = plan_protocol.sha256_json(audit)
        events: list[dict[str, object]] = []
        plan_protocol.append_plan_event(
            events,
            "protocol_initialized",
            {"plan_protocol_version": plan_protocol.PLAN_PROTOCOL_V2},
            recorded_at="2026-07-29T12:00:00Z",
            event_id="019f0000-0000-7000-8000-000000000096",
        )
        for offset, event_type in enumerate(
            (
                "candidate_linted",
                "audit_completed",
                "issue_read_back",
                "graph_policy_evaluated",
            ),
            97,
        ):
            plan_protocol.append_plan_event(
                events,
                event_type,
                {"evidence": event_type},
                recorded_at=f"2026-07-29T12:{offset - 90:02d}:00Z",
                event_id=f"00000000-0000-4000-8000-{offset:012d}",
            )
        manifest = {
            "run_id": "validator-v2-manifest",
            "parent_thread_id": "019f0000-0000-7000-8000-000000000094",
            "repo_root": "/repo-v2",
            "starting_commit": "d" * 40,
            "run_started_at": "2026-07-29T11:59:00Z",
            "workflow_version": plan_protocol.WORKFLOW_VERSION_V2,
            "plan_protocol_version": plan_protocol.PLAN_PROTOCOL_V2,
            "plan_events": events,
            "plan_audits": [audit],
            "graph_policy_receipt": policy,
        }
        plan_protocol.record_protocol_activation(manifest, events[0])
        return manifest

    def test_accepts_v2_no_graph_evidence(self):
        errors: list[str] = []
        manifest = self.manifest()
        evidence = {
            "final_message": (
                "PASS remote-body\n"
                + plan_protocol.plan_audit_callback_marker(
                    manifest["plan_audits"][0]
                )
            ),
            "delegation_started_at": "2026-07-29T12:05:00Z",
            "completed_at": "2026-07-29T12:06:00Z",
            "delegation_arguments": {
                "message": "Independent Plan spec auditor\nAudit the remote Plan."
            },
        }
        with patch.object(
            validator,
            "collaboration_delegated_audit_evidence",
            return_value=(evidence, None),
        ):
            validator.validate_plan_protocol_evidence(
                manifest, self.body, errors, skip_remote=True
            )
        self.assertEqual(errors, [])

    def test_authenticated_plan_audit_callback_binds_semantic_findings(self):
        manifest = self.manifest()
        audit = manifest["plan_audits"][0]
        forged_callback = (
            "BLOCKED remote-body: High PA-001\n"
            "PLAN_AUDIT_RECEIPT_SHA256: " + "0" * 64
        )
        audit["callback_sha256"] = hashlib.sha256(forged_callback.encode()).hexdigest()
        audit["result_sha256"] = plan_protocol.sha256_json(audit)
        evidence = {
            "final_message": forged_callback,
            "delegation_started_at": "2026-07-29T12:05:00Z",
            "completed_at": "2026-07-29T12:06:00Z",
            "delegation_arguments": {
                "message": "Independent Plan spec auditor\nAudit the remote Plan."
            },
        }
        errors: list[str] = []
        with patch.object(
            validator,
            "collaboration_delegated_audit_evidence",
            return_value=(evidence, None),
        ):
            validator.validate_plan_protocol_evidence(
                manifest, self.body, errors, skip_remote=True
            )
        self.assertTrue(
            any("exact semantic audit content" in error for error in errors)
        )

    def test_no_graph_rejects_all_graph_mutation_evidence(self):
        manifest = self.manifest()
        manifest.update(
            {
                "graph_draft": {"children": [{"task_id": "T-999"}]},
                "graph_authorization": {"authorized": True},
                "graph_actions": [
                    {"kind": "create_child", "key": "T-999", "state": "verified"}
                ],
                "graph_remote_state": {"children": [{"task_id": "T-999"}]},
            }
        )
        audit = manifest["plan_audits"][0]
        callback = (
            "PASS remote-body\n"
            + plan_protocol.plan_audit_callback_marker(audit)
        )
        audit["callback_sha256"] = hashlib.sha256(callback.encode()).hexdigest()
        audit["result_sha256"] = plan_protocol.sha256_json(audit)
        evidence = {
            "final_message": callback,
            "delegation_started_at": "2026-07-29T12:05:00Z",
            "completed_at": "2026-07-29T12:06:00Z",
            "delegation_arguments": {
                "message": "Independent Plan spec auditor\nAudit the remote Plan."
            },
        }
        errors: list[str] = []
        with patch.object(
            validator,
            "collaboration_delegated_audit_evidence",
            return_value=(evidence, None),
        ):
            validator.validate_plan_protocol_evidence(
                manifest, self.body, errors, skip_remote=True
            )
        self.assertTrue(any("NO_GRAPH requires graph_draft" in error for error in errors))
        self.assertTrue(any("NO_GRAPH requires graph_actions" in error for error in errors))

    def test_no_graph_rejects_graph_lifecycle_events(self):
        manifest = self.manifest()
        plan_protocol.append_plan_event(
            manifest["plan_events"],
            "graph_action_recorded",
            {"action": "create_child", "status": "verified"},
            recorded_at="2026-07-29T12:12:00Z",
            event_id="00000000-0000-4000-8000-000000000121",
        )
        audit = manifest["plan_audits"][0]
        callback = (
            "PASS remote-body\n"
            + plan_protocol.plan_audit_callback_marker(audit)
        )
        evidence = {
            "final_message": callback,
            "delegation_started_at": "2026-07-29T12:05:00Z",
            "completed_at": "2026-07-29T12:06:00Z",
            "delegation_arguments": {
                "message": "Independent Plan spec auditor\nAudit the remote Plan."
            },
        }
        errors: list[str] = []
        with patch.object(
            validator,
            "collaboration_delegated_audit_evidence",
            return_value=(evidence, None),
        ):
            validator.validate_plan_protocol_evidence(
                manifest, self.body, errors, skip_remote=True
            )
        self.assertTrue(
            any(
                "NO_GRAPH forbids graph_action_recorded" in error
                for error in errors
            )
        )

    def test_legacy_manifest_bypasses_v2_contract(self):
        errors: list[str] = []
        validator.validate_plan_protocol_evidence(
            {"plan_protocol_version": plan_protocol.PLAN_PROTOCOL_V1},
            "legacy body",
            errors,
            skip_remote=True,
        )
        self.assertEqual(errors, [])

    def test_hash_chained_v2_initialization_cannot_be_downgraded_to_v1(self):
        manifest = self.manifest()
        manifest["plan_protocol_version"] = plan_protocol.PLAN_PROTOCOL_V1
        errors: list[str] = []
        validator.validate_plan_protocol_evidence(
            manifest,
            self.body,
            errors,
            skip_remote=True,
        )
        self.assertTrue(any("cannot downgrade" in error for error in errors), errors)

    def test_v2_workflow_cannot_hide_downgrade_by_deleting_events(self):
        manifest = self.manifest()
        manifest["workflow_version"] = "evidence-gated-delivery/plan-protocol-v2"
        manifest["plan_protocol_version"] = plan_protocol.PLAN_PROTOCOL_V1
        manifest.pop("plan_events")
        errors: list[str] = []
        validator.validate_plan_protocol_evidence(
            manifest,
            self.body,
            errors,
            skip_remote=True,
        )
        self.assertTrue(any("cannot downgrade" in error for error in errors), errors)

    def test_hash_chained_v2_migration_cannot_be_downgraded_to_v1(self):
        manifest: dict[str, object] = {
            "run_id": "validator-migration-downgrade",
            "plan_protocol_version": plan_protocol.PLAN_PROTOCOL_V1,
            "plan_events": [],
        }
        plan_protocol.migrate_manifest_to_v2(
            manifest,
            recorded_at="2026-07-29T12:00:00Z",
            event_id="019f0000-0000-7000-8000-000000000199",
        )
        manifest["plan_protocol_version"] = plan_protocol.PLAN_PROTOCOL_V1
        errors: list[str] = []
        validator.validate_plan_protocol_evidence(
            manifest,
            self.body,
            errors,
            skip_remote=True,
        )
        self.assertTrue(any("cannot downgrade" in error for error in errors), errors)

    def test_migrated_v2_cannot_hide_downgrade_by_deleting_events(self):
        manifest: dict[str, object] = {
            "run_id": "validator-migration-deleted-events",
            "parent_thread_id": "019f0000-0000-7000-8000-000000000388",
            "repo_root": "/repo-a",
            "starting_commit": "a" * 40,
            "run_started_at": "2026-07-28T12:00:00Z",
            "workflow_version": "evidence-gated-delivery/continuous-improvement-v1",
            "plan_protocol_version": plan_protocol.PLAN_PROTOCOL_V1,
            "plan_events": [],
        }
        plan_protocol.migrate_manifest_to_v2(
            manifest,
            recorded_at="2026-07-29T12:00:00Z",
            event_id="019f0000-0000-7000-8000-000000000299",
        )
        manifest["workflow_version"] = (
            "evidence-gated-delivery/continuous-improvement-v1"
        )
        manifest["run_id"] = "replacement-run"
        manifest["repo_root"] = "/replacement-repo"
        manifest["starting_commit"] = "c" * 40
        manifest["run_started_at"] = "2020-01-01T00:00:00Z"
        manifest["plan_protocol_version"] = plan_protocol.PLAN_PROTOCOL_V1
        manifest["plan_events"] = []
        errors: list[str] = []
        validator.validate_plan_protocol_evidence(
            manifest,
            self.body,
            errors,
            skip_remote=True,
        )
        self.assertTrue(any("cannot downgrade" in error for error in errors), errors)
        self.assertTrue(any("run_id mismatch" in error for error in errors), errors)
        manifest.pop("run_id")
        missing_id_errors: list[str] = []
        validator.validate_plan_protocol_evidence(
            manifest,
            self.body,
            missing_id_errors,
            skip_remote=True,
        )
        self.assertTrue(
            any("cannot downgrade" in error for error in missing_id_errors),
            missing_id_errors,
        )
        self.assertTrue(
            any("run_id mismatch" in error for error in missing_id_errors),
            missing_id_errors,
        )

    def test_activation_receipt_enforces_repository_baseline(self):
        manifest: dict[str, object] = {
            "run_id": "validator-activation-baseline",
            "parent_thread_id": "019f0000-0000-7000-8000-000000000389",
            "repo_root": "/repo-a",
            "starting_commit": "b" * 40,
            "run_started_at": "2026-07-28T12:00:00Z",
            "workflow_version": "evidence-gated-delivery/continuous-improvement-v1",
            "plan_protocol_version": plan_protocol.PLAN_PROTOCOL_V1,
            "plan_events": [],
        }
        plan_protocol.migrate_manifest_to_v2(
            manifest,
            recorded_at="2026-07-29T12:00:00Z",
            event_id="019f0000-0000-7000-8000-000000000390",
        )
        manifest["repo_root"] = "/repo-b"
        active, errors = plan_protocol.validate_protocol_activation_receipt(manifest)
        self.assertTrue(active)
        self.assertTrue(any("repo_root mismatch" in error for error in errors), errors)

    def test_activation_receipt_enforces_workflow_and_protocol_bindings(self):
        workflow_manifest = self.manifest()
        workflow_manifest["workflow_version"] = "legacy-workflow"
        active, workflow_errors = (
            plan_protocol.validate_protocol_activation_receipt(workflow_manifest)
        )
        self.assertTrue(active)
        self.assertTrue(
            any("workflow_version mismatch" in error for error in workflow_errors),
            workflow_errors,
        )

        protocol_manifest = self.manifest()
        protocol_manifest["plan_protocol_version"] = plan_protocol.PLAN_PROTOCOL_V1
        active, protocol_errors = (
            plan_protocol.validate_protocol_activation_receipt(protocol_manifest)
        )
        self.assertTrue(active)
        self.assertTrue(
            any(
                "plan_protocol_version mismatch" in error
                for error in protocol_errors
            ),
            protocol_errors,
        )

    def test_v2_manifest_requires_external_activation_receipt(self):
        manifest = self.manifest()
        plan_protocol.protocol_activation_receipt_path(manifest["run_id"]).unlink()
        errors: list[str] = []
        validator.validate_plan_protocol_evidence(
            manifest,
            self.body,
            errors,
            skip_remote=True,
        )
        self.assertTrue(
            any("requires a durable external activation receipt" in error for error in errors),
            errors,
        )

    def test_v1_with_tampered_event_chain_fails_closed(self):
        manifest = self.manifest()
        manifest["plan_protocol_version"] = plan_protocol.PLAN_PROTOCOL_V1
        manifest["plan_events"][0]["payload"]["plan_protocol_version"] = (
            plan_protocol.PLAN_PROTOCOL_V1
        )
        errors: list[str] = []
        validator.validate_plan_protocol_evidence(
            manifest,
            self.body,
            errors,
            skip_remote=True,
        )
        self.assertTrue(any("event_sha256" in error for error in errors), errors)

    def test_graph_draft_must_match_authoritative_plan_tasks(self):
        authoritative_tasks = [
            {
                "task_id": "T-001",
                "title": "Authoritative task",
                "body": "- [ ] **T-001 — Authoritative task.**",
                "depends_on": [],
            }
        ]
        forged_tasks = [
            {
                "task_id": "T-001",
                "title": "Unrelated task",
                "body": "- [ ] **T-001 — Unrelated task.**",
                "depends_on": [],
            }
        ]
        forged = plan_protocol.freeze_graph_draft(
            "https://github.com/o/r/issues/1",
            "o/r",
            forged_tasks,
        )
        errors = validator.authoritative_graph_draft_errors(
            forged,
            "https://github.com/o/r/issues/1",
            "o/r",
            authoritative_tasks,
        )
        self.assertTrue(
            any("authoritative Plan tasks" in error for error in errors),
            errors,
        )

    def test_post_activation_manifest_cannot_omit_protocol_version(self):
        errors: list[str] = []
        validator.validate_plan_protocol_evidence(
            {"run_started_at": "2026-07-29T15:00:00Z"},
            "new body",
            errors,
            skip_remote=True,
        )
        self.assertTrue(any("plan_protocol_version" in error for error in errors))

    def test_backdated_timestamp_cannot_disguise_version_omission(self):
        errors: list[str] = []
        validator.validate_plan_protocol_evidence(
            {"run_started_at": "2020-01-01T00:00:00Z"},
            "backdated body",
            errors,
            skip_remote=True,
        )
        self.assertTrue(
            any("plan_protocol_version is required" in error for error in errors)
        )

    def test_checkpoint_only_manifest_cannot_satisfy_v2_plan_gate(self):
        events: list[dict[str, object]] = []
        plan_protocol.append_plan_event(
            events,
            "checkpoint_issued",
            {"status": "waiting"},
            recorded_at="2026-07-29T15:00:00Z",
            event_id="019f0000-0000-7000-8000-000000000099",
        )
        errors: list[str] = []
        validator.validate_plan_protocol_evidence(
            {
                "plan_protocol_version": plan_protocol.PLAN_PROTOCOL_V2,
                "plan_events": events,
            },
            self.body,
            errors,
            skip_remote=True,
        )
        self.assertTrue(any("candidate_linted" in error for error in errors))
        self.assertTrue(any("plan_audits" in error for error in errors))

    def test_privacy_sentinel_rejects_public_graph_secret(self):
        manifest = self.manifest()
        manifest["graph_authorization"] = {
            "authorization_evidence": "Bearer abcdefghijklmnopqrstuvwxyz"
        }
        errors: list[str] = []
        validator.validate_plan_protocol_evidence(
            manifest, self.body, errors, skip_remote=True
        )
        self.assertTrue(any("privacy sentinel" in error for error in errors))

    def test_invalid_recorded_graph_is_reported_without_validator_crash(self):
        manifest = self.manifest()
        graph_body = """# Plan

## Tasks

- [ ] **T-001 — First task.** Objective: first. Context: graph. Affected modules: `a`. Requirements: work. Verification: test. Complete when: done. Owner lane: core. `depends_on: []`.

- [ ] **T-002 — Second task.** Objective: second. Context: graph. Affected modules: `b`. Requirements: work. Verification: test. Complete when: done. Owner lane: core. `depends_on: [T-001]`.

## Out of Scope

No UI.
"""
        graph_tasks = plan_protocol.parse_tasks(graph_body)
        manifest.update(
            {
                "implementation_issue_url": "https://github.com/o/r/issues/1",
                "graph_policy_receipt": plan_protocol.evaluate_graph_policy(
                    graph_tasks, evaluated_at="2026-07-29T12:10:00Z"
                ),
                "graph_draft": {
                    "schema_version": "graph-draft/v1",
                    "repository": "o/r",
                    "parent_issue_url": "https://github.com/o/r/issues/1",
                    "children": [
                        {
                            "task_id": "T-001",
                            "stable_marker": "<!-- evidence-gated-delivery-task:T-001 -->",
                            "title": "Validate plan",
                            "body": "tampered",
                            "body_sha256": "0" * 64,
                        }
                    ],
                    "edges": [],
                    "draft_sha256": "0" * 64,
                },
                "graph_capability_receipt": {},
                "graph_remote_state": {"children": [], "edges": []},
            }
        )
        errors: list[str] = []
        validator.validate_plan_protocol_evidence(
            manifest, graph_body, errors, skip_remote=True
        )
        self.assertTrue(
            any("graph reconciliation evidence is invalid" in error for error in errors),
            errors,
        )


class RemoteGraphReadbackTests(unittest.TestCase):
    url = "https://github.com/o/r/issues/1"

    def payloads(self, *, symmetric: bool, connection_shape: bool = False):
        child_one_url = "https://github.com/o/r/issues/2"
        child_two_url = "https://github.com/o/r/issues/3"
        sub_issues = [{"number": 2}, {"number": 3}]
        child_one_blocking = [{"url": child_two_url}] if symmetric else []
        child_two_blocked_by = [{"url": child_one_url}]
        if connection_shape:
            sub_issues = {"nodes": sub_issues, "totalCount": 2}
            child_one_blocking = {
                "nodes": child_one_blocking,
                "totalCount": len(child_one_blocking),
            }
            child_two_blocked_by = {
                "nodes": child_two_blocked_by,
                "totalCount": len(child_two_blocked_by),
            }
        parent = {"url": self.url, "subIssues": sub_issues}
        child_one = {
            "url": child_one_url,
            "title": "One",
            "body": "<!-- evidence-gated-delivery-task:T-001 -->\n\none",
            "parent": {"url": self.url},
            "blockedBy": (
                {"nodes": [], "totalCount": 0} if connection_shape else []
            ),
            "blocking": child_one_blocking,
        }
        child_two = {
            "url": child_two_url,
            "title": "Two",
            "body": "<!-- evidence-gated-delivery-task:T-002 -->\n\ntwo",
            "parent": {"url": self.url},
            "blockedBy": child_two_blocked_by,
            "blocking": (
                {"nodes": [], "totalCount": 0} if connection_shape else []
            ),
        }
        return [(parent, None), (child_one, None), (child_two, None)]

    def test_requires_bidirectional_dependency_readback(self):
        with patch.object(
            validator, "_gh_json", side_effect=self.payloads(symmetric=True)
        ):
            state, error = validator._remote_graph_state(self.url)
        self.assertIsNone(error)
        self.assertEqual(
            state["edges"], [{"blocked": "T-002", "blocked_by": "T-001"}]
        )
        with patch.object(
            validator, "_gh_json", side_effect=self.payloads(symmetric=False)
        ):
            _state, error = validator._remote_graph_state(self.url)
        self.assertIn("symmetry mismatch", error)

    def test_accepts_github_cli_connection_shaped_relationships(self):
        with patch.object(
            validator,
            "_gh_json",
            side_effect=self.payloads(symmetric=True, connection_shape=True),
        ):
            state, error = validator._remote_graph_state(self.url)
        self.assertIsNone(error)
        self.assertEqual(
            state["edges"], [{"blocked": "T-002", "blocked_by": "T-001"}]
        )

    def test_accepts_empty_connection_shaped_subissues(self):
        with patch.object(
            validator,
            "_gh_json",
            return_value=(
                {
                    "url": self.url,
                    "subIssues": {"nodes": [], "totalCount": 0},
                },
                None,
            ),
        ):
            state, error = validator._remote_graph_state(self.url)
        self.assertIsNone(error)
        self.assertEqual(state, {"children": [], "edges": []})

    def test_no_graph_scan_accepts_empty_connection_shaped_subissues(self):
        with patch.object(
            validator,
            "_gh_json",
            return_value=(
                {"subIssues": {"nodes": [], "totalCount": 0}},
                None,
            ),
        ):
            children, error = validator._remote_workflow_graph_artifacts(self.url)
        self.assertIsNone(error)
        self.assertEqual(children, [])


if __name__ == "__main__":
    unittest.main()
