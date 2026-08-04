from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dependency_readiness import (
    canonical_phase_receipt_replay,
    legacy_semantic_entry_gates,
    transitive_deferred_tasks,
    validate_dependency_readiness_evidence,
)
from plan_tasks import PlanProtocolError, issue_body_sha256, parse_tasks
from review_phase_validation import validate_predecessor_dependency_readiness
from collaboration_receipts import validate_task_authorization_evidence


AUTHORITY_URL = "https://github.com/mknoth197/evidence-gated-delivery/issues/10"
AUTHORITY_BODY = "# Upstream authority\n\nValidated evidence.\n"
GATE_JSON = (
    '[{"authority_url":"' + AUTHORITY_URL
    + '","gate_id":"G-001","predicates":["phase_receipt:plan:VALID"]}]'
)


def plan(*, structured: bool = True, legacy_phrase: bool = False) -> str:
    gate = f" `entry_gates: {GATE_JSON}`." if structured else ""
    requirement = (
        "hard entry-gate read-back of issue 10 VALID Plan"
        if legacy_phrase
        else "consume the declared upstream contract"
    )
    return f"""## Tasks

- [ ] **T-001 — Freeze contracts.** Objective: define contracts. Context: local workflow. Affected modules: `contracts.py`. Requirements: freeze exact schemas. Verification: test schemas. Complete when: schemas pass. Owner lane: core.{' `entry_gates: []`.' if structured else ''} `depends_on: []`.

- [ ] **T-002 — Consume upstream proof.** Objective: consume the proof. Context: provider boundary. Affected modules: `provider.py`. Requirements: {requirement}. Verification: test exact pins. Complete when: upstream evidence is bound. Owner lane: provider.{gate} `depends_on: [T-001]`.

- [ ] **T-003 — Integrate workflow.** Objective: integrate the result. Context: end-to-end workflow. Affected modules: `workflow.py`. Requirements: use the provider result. Verification: run end-to-end tests. Complete when: all behavior passes. Owner lane: integration.{' `entry_gates: []`.' if structured else ''} `depends_on: [T-002]`.
"""


def authority_reader(_url: str):
    return {"state": "OPEN", "body": AUTHORITY_BODY}, None


def write_json(path: Path, value: dict) -> str:
    raw = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def add_classification(data: dict, body: str) -> None:
    tasks = parse_tasks(body, require_entry_gates=True)
    classifications = [
        {
            "task_id": task["task_id"],
            "disposition": "gated" if task["entry_gates"] else "none",
            "gate_ids": [gate["gate_id"] for gate in task["entry_gates"]],
        }
        for task in tasks
    ]
    body_sha = issue_body_sha256(body)
    digest = hashlib.sha256(
        json.dumps(
            {"authoritative_issue_body_sha256": body_sha, "classifications": classifications},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    marker = f"DEPENDENCY-CLASSIFICATION:{digest}:PASS"
    callback_sha = "c" * 64
    data["dependency_classification_evidence"] = {
        "policy_version": "dependency-classification/v1",
        "authoritative_issue_body_sha256": body_sha,
        "classifications": classifications,
        "audit_agent_id": "independent-plan-auditor",
        "audit_callback_sha256": callback_sha,
        "audit_marker": marker,
    }
    data["plan_audits"] = [
        {
            "agent_id": "independent-plan-auditor",
            "status": "completed",
            "kind": "final_remote",
            "receipt_kind": "collaboration_delegated",
            "reviewed_body_sha256": body_sha,
            "callback_sha256": callback_sha,
            "evidence_ids": [marker],
        }
    ]


def ready_manifest(tmp: Path, body: str) -> dict:
    manifest_path = tmp / "plan-manifest.json"
    manifest_sha = write_json(
        manifest_path,
        {
            "phase": "plan",
            "run_id": "upstream-plan",
            "implementation_issue_url": AUTHORITY_URL,
        },
    )
    receipt_path = tmp / "plan-receipt.json"
    receipt_sha = write_json(
        receipt_path,
        {
            "phase": "plan",
            "status": "VALID",
            "manifest": str(manifest_path),
            "manifest_sha256": manifest_sha,
            "remote_verification": True,
            "errors": [],
            "validated_at": "2026-08-04T19:59:00Z",
            "receipt_path": str(receipt_path),
        },
    )
    data = {
        "dependency_readiness_evidence_required": True,
        "dependency_readiness_evidence": {
            "policy_version": "dependency-readiness/v1",
            "authoritative_issue_body_sha256": issue_body_sha256(body),
            "status": "READY",
            "gates": [
                {
                    "gate_id": "G-001",
                    "task_id": "T-002",
                    "authority_url": AUTHORITY_URL,
                    "authority_state": "OPEN",
                    "authority_body_sha256": hashlib.sha256(
                        AUTHORITY_BODY.encode()
                    ).hexdigest(),
                    "verified_at": "2026-08-04T20:00:00Z",
                    "predicates": [
                        {
                            "predicate": "phase_receipt:plan:VALID",
                            "state": "verified",
                            "receipt_path": str(receipt_path),
                            "receipt_sha256": receipt_sha,
                            "manifest_path": str(manifest_path),
                            "manifest_sha256": manifest_sha,
                        }
                    ],
                }
            ],
            "deferred_task_ids": [],
        },
    }
    add_classification(data, body)
    return data


class DependencyReadinessTests(unittest.TestCase):
    def test_new_plan_requires_explicit_entry_gate_field(self):
        with self.assertRaisesRegex(PlanProtocolError, "missing exact trailing entry_gates"):
            parse_tasks(plan(structured=False), require_entry_gates=True)

    def test_rephrasing_cannot_bypass_mandatory_structured_gate(self):
        rewritten = plan(structured=False).replace(
            "consume the declared upstream contract", "wait for the other work"
        )
        with self.assertRaisesRegex(PlanProtocolError, "missing exact trailing entry_gates"):
            parse_tasks(rewritten, require_entry_gates=True)

    def test_entry_gate_json_must_be_canonical(self):
        noncanonical = plan().replace(
            '`entry_gates: []`', '`entry_gates: [ ]`', 1
        )
        with self.assertRaisesRegex(PlanProtocolError, "canonical compact"):
            parse_tasks(noncanonical, require_entry_gates=True)

    def test_legacy_semantic_gate_requires_plan_repair(self):
        body = plan(structured=False, legacy_phrase=True)
        tasks = parse_tasks(body)
        self.assertEqual(legacy_semantic_entry_gates(tasks), ["T-002"])
        self.assertEqual(
            validate_dependency_readiness_evidence({}, body, tasks),
            [
                "semantic external prerequisites require Plan repair with typed entry_gates: T-002"
            ],
        )

    def test_empty_structured_gate_cannot_mask_semantic_prerequisite(self):
        body = plan(legacy_phrase=True).replace(GATE_JSON, "[]")
        tasks = parse_tasks(body, require_entry_gates=True)
        self.assertEqual(legacy_semantic_entry_gates(tasks), ["T-002"])
        data = {"dependency_readiness_evidence_required": True}
        add_classification(data, body)
        self.assertEqual(
            validate_dependency_readiness_evidence(
                data,
                body,
                tasks,
                require_structured=True,
            ),
            [
                "semantic external prerequisites require Plan repair with typed entry_gates: T-002"
            ],
        )

    def test_rephrased_empty_gate_requires_independent_classification(self):
        body = plan().replace(GATE_JSON, "[]").replace(
            "consume the declared upstream contract",
            "work is eligible only when issue 10 has published a VALID plan",
        )
        tasks = parse_tasks(body, require_entry_gates=True)
        self.assertEqual(
            validate_dependency_readiness_evidence(
                {"dependency_readiness_evidence_required": True},
                body,
                tasks,
                require_structured=True,
            ),
            ["structured entry gates require dependency classification evidence"],
        )

    def test_dependency_classification_audit_cannot_replay_across_body_change(self):
        old_body = plan().replace(GATE_JSON, "[]")
        new_body = old_body.replace(
            "consume the declared upstream contract",
            "work is eligible only when issue 10 has published a VALID plan",
        )
        data = {"dependency_readiness_evidence_required": True}
        add_classification(data, new_body)
        data["plan_audits"][0]["reviewed_body_sha256"] = issue_body_sha256(old_body)
        errors = validate_dependency_readiness_evidence(
            data,
            new_body,
            require_structured=True,
        )
        self.assertIn(
            "dependency classification lacks a completed independent Plan audit binding",
            errors,
        )

    def test_preliminary_classification_cannot_substitute_for_final_remote_audit(self):
        body = plan().replace(GATE_JSON, "[]")
        data = {"dependency_readiness_evidence_required": True}
        add_classification(data, body)
        preliminary = dict(data["plan_audits"][0])
        preliminary["kind"] = "preliminary"
        final_remote = dict(data["plan_audits"][0])
        final_remote["agent_id"] = "final-auditor"
        final_remote["callback_sha256"] = "d" * 64
        final_remote["evidence_ids"] = ["OTHER-EVIDENCE"]
        data["plan_audits"] = [preliminary, final_remote]
        errors = validate_dependency_readiness_evidence(
            data, body, require_structured=True
        )
        self.assertIn(
            "dependency classification lacks a completed independent Plan audit binding",
            errors,
        )

    def test_plan_24_regression_defers_t006_t008_and_t009(self):
        body = (ROOT / "tests" / "fixtures" / "issue-24-tasks.md").read_text()
        self.assertEqual(
            hashlib.sha256(body.encode()).hexdigest(),
            "7c7b91fa6c009fd023a7885127c5cefd9db3237b13103bc2ec71408bef13a570",
        )
        tasks = parse_tasks(body)
        self.assertEqual(legacy_semantic_entry_gates(tasks), ["T-006"])
        self.assertEqual(
            transitive_deferred_tasks(tasks, {"T-006"}),
            ["T-006", "T-008", "T-009"],
        )
        predecessor = {"implementation_issue_url": "https://github.com/mknoth197/evidence-gated-delivery/issues/24"}
        errors: list[str] = []
        deps = SimpleNamespace(
            github_readback=lambda _url, _kind: (body, None),
            dependency_authority_reader=authority_reader,
            dependency_interface_reader=lambda *_args: (None, "unused"),
            phase_receipt_verifier=lambda _manifest, _phase: [],
            dependency_authorization_verifier=lambda _data, _auth, _tasks: [],
        )
        validate_predecessor_dependency_readiness(
            {}, predecessor, errors, False, deps=deps
        )
        self.assertEqual(
            errors,
            [
                "semantic external prerequisites require Plan repair with typed entry_gates: T-006"
            ],
        )

    def test_live_authority_and_phase_receipt_allow_ready(self):
        body = plan()
        with tempfile.TemporaryDirectory() as directory:
            data = ready_manifest(Path(directory), body)
            self.assertEqual(
                validate_dependency_readiness_evidence(
                    data,
                    body,
                    require_structured=True,
                    authority_reader=authority_reader,
                    phase_receipt_verifier=lambda _manifest, _phase: [],
                ),
                [],
            )

    def test_mutated_authority_is_rejected(self):
        body = plan()
        with tempfile.TemporaryDirectory() as directory:
            data = ready_manifest(Path(directory), body)
            errors = validate_dependency_readiness_evidence(
                data,
                body,
                require_structured=True,
                authority_reader=lambda _url: (
                    {"state": "CLOSED", "body": "changed"},
                    None,
                ),
                phase_receipt_verifier=lambda _manifest, _phase: [],
            )
        self.assertIn(
            "dependency_readiness_evidence.gates[0].authority_state does not match live authority",
            errors,
        )
        self.assertIn(
            "dependency_readiness_evidence.gates[0].authority_body_sha256 does not match live authority bytes",
            errors,
        )

    def test_forged_receipt_or_manifest_digest_is_rejected(self):
        body = plan()
        with tempfile.TemporaryDirectory() as directory:
            data = ready_manifest(Path(directory), body)
            evidence = data["dependency_readiness_evidence"]["gates"][0]["predicates"][0]
            evidence["receipt_sha256"] = "0" * 64
            errors = validate_dependency_readiness_evidence(
                data,
                body,
                authority_reader=authority_reader,
                phase_receipt_verifier=lambda _manifest, _phase: [],
            )
        self.assertTrue(any("receipt_path SHA-256" in error for error in errors))

    def test_hand_authored_valid_receipt_fails_independent_replay(self):
        body = plan()
        with tempfile.TemporaryDirectory() as directory:
            data = ready_manifest(Path(directory), body)
            errors = validate_dependency_readiness_evidence(
                data,
                body,
                authority_reader=authority_reader,
                phase_receipt_verifier=lambda _manifest, _phase: [
                    "canonical upstream validator rejected manifest"
                ],
            )
        self.assertTrue(any("validator replay failed" in error for error in errors))

    def test_canonical_replay_invokes_validator_and_rejects_cycles(self):
        manifest = {"run_id": "upstream-replay"}
        calls = []

        def validator(value, phase, skip_remote):
            calls.append((value, phase, skip_remote))
            return canonical_phase_receipt_replay(value, phase, validator)

        self.assertEqual(
            canonical_phase_receipt_replay(manifest, "plan", validator),
            ["cyclic phase-receipt replay detected"],
        )
        self.assertEqual(calls, [(manifest, "plan", False)])

    def test_partial_authorization_is_authenticated_from_parent_trace(self):
        message = "Authorize partial implementation for T-001 only."
        authorization = {
            "receipt_kind": "authenticated_parent_user_message",
            "parent_thread_id": "parent-24",
            "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
            "received_at": "2026-08-04T20:01:00Z",
            "quote": message,
            "authorized_task_ids": ["T-001"],
        }
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "sessions" / "2026" / "rollout-parent-24.jsonl"
            session.parent.mkdir(parents=True)
            session.write_text(
                json.dumps(
                    {
                        "type": "response_item",
                        "timestamp": authorization["received_at"],
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": message}],
                        },
                    }
                )
                + "\n"
            )
            with patch.dict(os.environ, {"CODEX_HOME": directory}):
                self.assertEqual(
                    validate_task_authorization_evidence(
                        {"parent_thread_id": "parent-24"},
                        authorization,
                        ["T-001"],
                    ),
                    [],
                )
                authorization["message_sha256"] = "0" * 64
                self.assertTrue(
                    validate_task_authorization_evidence(
                        {"parent_thread_id": "parent-24"},
                        authorization,
                        ["T-001"],
                    )
                )
                authorization["message_sha256"] = hashlib.sha256(message.encode()).hexdigest()
                with session.open("a") as stream:
                    stream.write(
                        json.dumps(
                            {
                                "type": "response_item",
                                "timestamp": "2026-08-04T20:02:00Z",
                                "payload": {
                                    "type": "message",
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "input_text",
                                            "text": "Stop; revoke that authorization.",
                                        }
                                    ],
                                },
                            }
                        )
                        + "\n"
                    )
                self.assertTrue(
                    validate_task_authorization_evidence(
                        {"parent_thread_id": "parent-24"}, authorization, ["T-001"]
                    )
                )

    def test_partial_authorization_rejects_denial_discussion_and_quoted_text(self):
        messages = (
            "T-001 has an issue; do not implement it.",
            "We discussed implementing T-001 yesterday.",
            "Implement T-001 is the proposal we should discuss.",
            '"Authorize partial implementation for T-001 only."',
        )
        for message in messages:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                session = Path(directory) / "sessions" / "rollout-parent-24.jsonl"
                session.parent.mkdir(parents=True)
                session.write_text(
                    json.dumps(
                        {
                            "type": "response_item",
                            "timestamp": "2026-08-04T20:01:00Z",
                            "payload": {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": message}],
                            },
                        }
                    )
                    + "\n"
                )
                authorization = {
                    "receipt_kind": "authenticated_parent_user_message",
                    "parent_thread_id": "parent-24",
                    "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
                    "received_at": "2026-08-04T20:01:00Z",
                    "quote": message,
                    "authorized_task_ids": ["T-001"],
                }
                with patch.dict(os.environ, {"CODEX_HOME": directory}):
                    self.assertTrue(
                        validate_task_authorization_evidence(
                            {"parent_thread_id": "parent-24"},
                            authorization,
                            ["T-001"],
                        )
                    )

    def test_merged_interface_requires_live_pinned_bytes_and_version(self):
        body = plan().replace(
            "phase_receipt:plan:VALID", "merged_interface:delegation-proof/v1"
        )
        interface = b'{"version":"delegation-proof/v1"}'
        with tempfile.TemporaryDirectory() as directory:
            data = ready_manifest(Path(directory), body)
            data["dependency_readiness_evidence"][
                "authoritative_issue_body_sha256"
            ] = issue_body_sha256(body)
            predicate = data["dependency_readiness_evidence"]["gates"][0][
                "predicates"
            ][0]
            predicate.clear()
            predicate.update(
                {
                    "predicate": "merged_interface:delegation-proof/v1",
                    "state": "verified",
                    "repository": "mknoth197/evidence-gated-delivery",
                    "commit_sha": "a" * 40,
                    "interface_path": "contracts/delegation-proof.json",
                    "blob_sha256": hashlib.sha256(interface).hexdigest(),
                }
            )
            self.assertEqual(
                validate_dependency_readiness_evidence(
                    data,
                    body,
                    authority_reader=authority_reader,
                    interface_reader=lambda *_args: (interface, None),
                ),
                [],
            )
            errors = validate_dependency_readiness_evidence(
                data,
                body,
                authority_reader=authority_reader,
                interface_reader=lambda *_args: (b'{"version":"v2"}', None),
            )
        self.assertTrue(any("blob_sha256" in error for error in errors))
        self.assertTrue(any("do not contain delegation-proof/v1" in error for error in errors))

    def test_partial_only_requires_exact_closure_and_external_approval_record(self):
        body = plan()
        with tempfile.TemporaryDirectory() as directory:
            data = ready_manifest(Path(directory), body)
            receipt = data["dependency_readiness_evidence"]
            receipt["status"] = "PARTIAL_ONLY"
            predicate = receipt["gates"][0]["predicates"][0]
            predicate.clear()
            predicate.update(
                {
                    "predicate": "phase_receipt:plan:VALID",
                    "state": "blocked",
                    "reason": "upstream Plan is not VALID",
                }
            )
            receipt["deferred_task_ids"] = ["T-002", "T-003"]
            receipt["partial_scope_task_ids"] = ["T-001"]
            approval = {
                "parent_thread_id": "thread-24",
                "quote": "Implement only T-001.",
                "received_at": "2026-08-04T20:01:00Z",
                "authorized_task_ids": ["T-001"],
            }
            receipt["partial_authorization"] = approval
            data["parent_thread_id"] = "thread-24"
            self.assertEqual(
                validate_dependency_readiness_evidence(
                    data,
                    body,
                    authority_reader=authority_reader,
                    authorization_verifier=lambda _data, _auth, _tasks: [],
                ),
                [],
            )
            receipt["partial_authorization"]["authorized_task_ids"] = []
            errors = validate_dependency_readiness_evidence(
                data,
                body,
                authority_reader=authority_reader,
                authorization_verifier=lambda _data, _auth, _tasks: [],
            )
        self.assertIn(
            "PARTIAL_ONLY authorization must bind the exact executable task set", errors
        )

    def test_blocked_stops_plan_exit_with_transitive_closure(self):
        body = plan()
        with tempfile.TemporaryDirectory() as directory:
            data = ready_manifest(Path(directory), body)
            receipt = data["dependency_readiness_evidence"]
            receipt["status"] = "BLOCKED"
            predicate = receipt["gates"][0]["predicates"][0]
            predicate.clear()
            predicate.update(
                {
                    "predicate": "phase_receipt:plan:VALID",
                    "state": "blocked",
                    "reason": "upstream Plan is not VALID",
                }
            )
            errors = validate_dependency_readiness_evidence(
                data, body, authority_reader=authority_reader
            )
        self.assertIn(
            "dependency readiness blocks Plan exit and Implement Orientation: T-002, T-003",
            errors,
        )

    def test_orientation_rechecks_legacy_remote_plan(self):
        body = plan(structured=False, legacy_phrase=True)
        predecessor = {"implementation_issue_url": AUTHORITY_URL}
        errors: list[str] = []
        deps = SimpleNamespace(
            github_readback=lambda _url, _kind: (body, None),
            dependency_authority_reader=authority_reader,
            dependency_interface_reader=lambda *_args: (None, "unused"),
            phase_receipt_verifier=lambda _manifest, _phase: [],
            dependency_authorization_verifier=lambda _data, _auth, _tasks: [],
        )
        validate_predecessor_dependency_readiness(
            {}, predecessor, errors, False, deps=deps
        )
        self.assertEqual(
            errors,
            [
                "semantic external prerequisites require Plan repair with typed entry_gates: T-002"
            ],
        )


if __name__ == "__main__":
    unittest.main()
