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
        self.assertFalse(
            validator.persisted_delegation_role_matches(
                json.dumps(
                    {"task_name": "generic_review", "message": "gAAAAABencrypted"}
                ),
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
        self.assertFalse(
            validator.persisted_delegation_role_matches(
                {
                    "task_name": "plan_transition_judge",
                    "message": "gAAAAABencrypted",
                },
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
            "callback_sha256": hashlib.sha256(b"PASS remote-body").hexdigest(),
        }
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
        return {
            "plan_protocol_version": plan_protocol.PLAN_PROTOCOL_V2,
            "plan_events": events,
            "plan_audits": [audit],
            "graph_policy_receipt": policy,
        }

    def test_accepts_v2_no_graph_evidence(self):
        errors: list[str] = []
        evidence = {
            "final_message": "PASS remote-body",
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
                self.manifest(), self.body, errors, skip_remote=True
            )
        self.assertEqual(errors, [])

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
            "workflow_version": "evidence-gated-delivery/continuous-improvement-v1",
            "plan_protocol_version": plan_protocol.PLAN_PROTOCOL_V1,
            "plan_events": [],
        }
        plan_protocol.migrate_manifest_to_v2(
            manifest,
            recorded_at="2026-07-29T12:00:00Z",
            event_id="019f0000-0000-7000-8000-000000000299",
        )
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


class RemoteGraphReadbackTests(unittest.TestCase):
    url = "https://github.com/o/r/issues/1"

    def payloads(self, *, symmetric: bool):
        child_one_url = "https://github.com/o/r/issues/2"
        child_two_url = "https://github.com/o/r/issues/3"
        parent = {"url": self.url, "subIssues": [{"number": 2}, {"number": 3}]}
        child_one = {
            "url": child_one_url,
            "title": "One",
            "body": "<!-- evidence-gated-delivery-task:T-001 -->\n\none",
            "parent": {"url": self.url},
            "blockedBy": [],
            "blocking": [{"url": child_two_url}] if symmetric else [],
        }
        child_two = {
            "url": child_two_url,
            "title": "Two",
            "body": "<!-- evidence-gated-delivery-task:T-002 -->\n\ntwo",
            "parent": {"url": self.url},
            "blockedBy": [{"url": child_one_url}],
            "blocking": [],
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


class ReviewValidatorTests(unittest.TestCase):
    def test_full_review_validator_fails_when_repo_evidence_is_missing(self):
        errors = validator.validate(
            {
                "mode": "review",
                "run_started_at": "2026-07-29T12:00:00Z",
                "phase_timeline": {},
                "trace_audits": [],
            },
            "review",
            skip_remote=True,
        )
        self.assertTrue(
            any("actual-diff binding" in error for error in errors),
            errors,
        )

    def make_repo(self, root: Path) -> tuple[str, str]:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "review@example.invalid"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Review Test"],
            cwd=root,
            check=True,
        )
        (root / "base.txt").write_text("base\n")
        subprocess.run(["git", "add", "base.txt"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        (root / "changed.txt").write_text("changed\n")
        subprocess.run(["git", "add", "changed.txt"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "head"], cwd=root, check=True)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return base, head

    def test_changed_paths_bind_exact_live_pr_commits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base, head = self.make_repo(root)
            (root / "uncommitted.txt").write_text("not in the PR\n")
            errors: list[str] = []
            with patch.object(
                validator,
                "github_pr_oids",
                return_value=({"base_oid": base, "head_oid": head}, None),
            ):
                paths = validator.review_changed_paths(
                    {
                        "repo_root": str(root),
                        "starting_commit": base,
                        "pull_request_url": "https://github.com/o/r/pull/1",
                    },
                    errors,
                )
        self.assertEqual(errors, [])
        self.assertEqual(paths, ["changed.txt"])

    def test_changed_paths_fail_closed_on_pr_base_or_head_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base, head = self.make_repo(root)
            errors: list[str] = []
            with patch.object(
                validator,
                "github_pr_oids",
                return_value=({"base_oid": head, "head_oid": base}, None),
            ):
                validator.review_changed_paths(
                    {
                        "repo_root": str(root),
                        "starting_commit": base,
                        "pull_request_url": "https://github.com/o/r/pull/1",
                    },
                    errors,
                )
        self.assertTrue(any("starting_commit" in error for error in errors), errors)
        self.assertTrue(any("local HEAD" in error for error in errors), errors)


class TransitionJudgeSeparationTests(unittest.TestCase):
    def test_every_incompatible_role_and_other_transition_is_excluded(self):
        current = {"phase": "plan", "agent_id": "current"}
        data = {
            "contestants": [{"agent_id": " contestant "}],
            "judges": [{"agent_id": "tournament-judge"}],
            "implementation_workers": [{"agent_id": "worker"}],
            "trace_audits": [{"agent_id": "trace-auditor"}],
            "plan_audits": [{"agent_id": "plan-auditor"}],
            "test_reviewer": {"agent_id": "test-reviewer"},
            "acceptance_reviewer": {"agent_id": "acceptance-reviewer"},
            "phase_retrospectives": [{"agent_id": "retrospective"}],
            "phase_transition_judgments": [
                current,
                {"phase": "research", "agent_id": "prior-transition"},
            ],
        }
        self.assertEqual(
            validator.transition_judge_excluded_ids(data, current),
            {
                "contestant",
                "tournament-judge",
                "worker",
                "trace-auditor",
                "plan-auditor",
                "test-reviewer",
                "acceptance-reviewer",
                "retrospective",
                "prior-transition",
            },
        )


if __name__ == "__main__":
    unittest.main()
