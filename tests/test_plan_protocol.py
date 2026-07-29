from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "plan_protocol.py"
    spec = importlib.util.spec_from_file_location("plan_protocol", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


protocol = load_module()


def task(
    number: int,
    *,
    owner: str = "core",
    dependencies: tuple[int, ...] = (),
    title: str | None = None,
) -> str:
    task_id = f"T-{number:03d}"
    dependency_text = ", ".join(f"T-{value:03d}" for value in dependencies)
    return (
        f"- [ ] **{task_id} — {title or f'Task {number}'}.** "
        f"Objective: objective {number}. Context: context {number}. "
        f"Affected modules: `scripts/{number}.py`, `tests/test_{number}.py`. "
        f"Requirements: requirement {number}. Verification: verification {number}. "
        f"Complete when: completion {number}. Owner lane: {owner}. "
        f"`depends_on: [{dependency_text}]`."
    )


def body(*tasks: str, newline: str = "\n") -> str:
    return newline.join(
        (
            "# Plan",
            "",
            "## Problem Statement",
            "Problem.",
            "## Personas",
            "Operator.",
            "## Value Assessment",
            "Value.",
            "## User Stories",
            "Story.",
            "## Design",
            "```mermaid",
            "flowchart LR",
            "  A --> B",
            "```",
            "## Tasks",
            "",
            *tasks,
            "",
            "## Out of Scope",
            "",
            "- Nothing",
            "## Acceptance Criteria",
            "- WHEN invoked, THE SYSTEM SHALL validate.",
            "## Mockup Accounting Matrix",
            "| Scope | Criterion | Task | Verification |",
            "|---|---|---|---|",
            "| Protocol | AC | T-001 | test |",
            "## Cross-Reference",
            "https://github.com/example/repo/issues/1",
        )
    )


def audit_hash(audit: dict) -> str:
    return protocol.sha256_json(
        {key: value for key, value in audit.items() if key != "result_sha256"}
    )


def finding(
    finding_id: str,
    severity: str,
    disposition: str,
    **extra,
) -> dict:
    value = {
        "finding_id": finding_id,
        "severity": severity,
        "confidence": 9,
        "evidence": "exact issue text",
        "bounded_question": None,
        "targeted_patch": "replace exact clause",
        "verification_implication": "rerun exact fixture",
        "downstream_instruction": "preserve invariant",
        "disposition": disposition,
    }
    value.update(extra)
    return value


def audit(
    audit_id: str,
    kind: str,
    body_hash: str,
    findings: list[dict],
    *,
    predecessor: str | None = None,
    predecessor_findings: list[str] | None = None,
) -> dict:
    value = {
        "audit_id": audit_id,
        "agent_id": str(uuid.uuid4()),
        "agent_path": f"/root/{audit_id}",
        "role_marker": "Independent Plan spec auditor",
        "kind": kind,
        "reviewed_body_sha256": body_hash,
        "started_at": "2026-07-29T10:00:00Z",
        "completed_at": "2026-07-29T10:01:00Z",
        "evidence_ids": ["E-001"],
        "findings": findings,
        "predecessor_audit_id": predecessor,
        "predecessor_finding_ids": predecessor_findings or [],
    }
    value["result_sha256"] = audit_hash(value)
    return value


class CanonicalBodyTests(unittest.TestCase):
    def test_only_line_endings_are_normalized(self):
        lf = "a \n b\n"
        self.assertEqual(
            protocol.issue_body_sha256(lf),
            protocol.issue_body_sha256(lf.replace("\n", "\r\n")),
        )
        self.assertNotEqual(
            protocol.issue_body_sha256(lf),
            protocol.issue_body_sha256(lf.strip()),
        )

    def test_hash_is_canonical_utf8_sha256(self):
        canonical = "café\n"
        self.assertEqual(
            protocol.issue_body_sha256("café\r"),
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )


class TaskAndPolicyTests(unittest.TestCase):
    def test_parses_complete_multiline_tasks_and_policy_boundary(self):
        markdown = body(
            task(1),
            task(2).replace(" Context:", "\n  Context:"),
            task(3),
        )
        parsed = protocol.parse_tasks(markdown)
        self.assertEqual([item["task_id"] for item in parsed], ["T-001", "T-002", "T-003"])
        self.assertEqual(parsed[0]["affected_modules"], ["scripts/1.py", "tests/test_1.py"])
        receipt = protocol.evaluate_graph_policy(
            parsed, evaluated_at="2026-07-29T10:00:00Z"
        )
        self.assertEqual(receipt["disposition"], "NO_GRAPH")
        self.assertEqual(receipt["edge_count"], 0)

    def test_graph_required_for_four_tasks_edge_or_second_lane(self):
        cases = (
            body(task(1), task(2), task(3), task(4)),
            body(task(1), task(2, dependencies=(1,))),
            body(task(1), task(2, owner="skills")),
        )
        for markdown in cases:
            with self.subTest(markdown=markdown):
                self.assertEqual(
                    protocol.evaluate_graph_policy(
                        protocol.parse_tasks(markdown),
                        evaluated_at="2026-07-29T10:00:00Z",
                    )["disposition"],
                    "GRAPH_REQUIRED",
                )

    def test_rejects_malformed_nested_duplicate_missing_and_cycle(self):
        malformed = task(1).replace("Objective:", "Goal:")
        nested = "  " + task(1)
        duplicate = body(task(1), task(1))
        missing = body(task(1, dependencies=(2,)))
        cycle = body(task(1, dependencies=(2,)), task(2, dependencies=(1,)))
        nonsequential = body(task(2))
        completed = body(task(1).replace("- [ ]", "- [x]", 1))
        for markdown in (
            body(malformed),
            body(nested),
            duplicate,
            missing,
            cycle,
            nonsequential,
            completed,
        ):
            with self.subTest(markdown=markdown):
                with self.assertRaises(protocol.PlanProtocolError):
                    protocol.parse_tasks(markdown)

    def test_task_boundaries_do_not_absorb_next_section(self):
        parsed = protocol.parse_tasks(body(task(1), task(2)))
        self.assertNotIn("Out of Scope", parsed[-1]["body"])
        self.assertTrue(parsed[-1]["body"].endswith("`."))

    def test_lint_is_deterministic_and_bound_to_candidate_hash(self):
        invalid = body(task(1).replace("Owner lane:", "Owner:"))
        first = protocol.lint_plan(invalid)
        second = protocol.lint_plan(invalid)
        self.assertEqual(first, second)
        self.assertEqual(first["candidate_body_sha256"], protocol.issue_body_sha256(invalid))
        self.assertEqual(first["status"], "FAIL")

    def test_lint_rejects_missing_structure_ears_mermaid_and_checked_tasks(self):
        invalid = "# Plan\n\n## Tasks\n\n" + task(1).replace("- [ ]", "- [x]", 1)
        finding_ids = {
            finding["finding_id"] for finding in protocol.lint_plan(invalid)["findings"]
        }
        self.assertEqual(
            finding_ids,
            {"LINT-001", "LINT-002", "LINT-003", "LINT-004", "LINT-005"},
        )


class EventAndMigrationTests(unittest.TestCase):
    def test_append_and_validate_hash_chain(self):
        events: list[dict] = []
        first = protocol.append_plan_event(
            events,
            "protocol_initialized",
            {"version": protocol.PLAN_PROTOCOL_V2},
            recorded_at="2026-07-29T10:00:00Z",
            event_id="00000000-0000-4000-8000-000000000001",
        )
        second = protocol.append_plan_event(
            events,
            "candidate_linted",
            {"status": "PASS"},
            recorded_at="2026-07-29T10:01:00Z",
            event_id="00000000-0000-4000-8000-000000000002",
        )
        self.assertEqual(second["previous_event_sha256"], first["event_sha256"])
        self.assertEqual(protocol.validate_plan_events(events), [])

    def test_mutation_and_duplicate_event_are_rejected(self):
        events: list[dict] = []
        protocol.append_plan_event(
            events,
            "candidate_linted",
            {"status": "PASS"},
            recorded_at="2026-07-29T10:00:00Z",
            event_id="00000000-0000-4000-8000-000000000001",
        )
        changed = copy.deepcopy(events)
        changed[0]["payload"]["status"] = "FAIL"
        self.assertTrue(any("event_sha256" in error for error in protocol.validate_plan_events(changed)))
        duplicate = copy.deepcopy(events)
        duplicate.append(copy.deepcopy(events[0]))
        duplicate[1]["sequence"] = 2
        duplicate[1]["previous_event_sha256"] = duplicate[0]["event_sha256"]
        duplicate[1]["event_sha256"] = protocol._event_hash(duplicate[1])
        self.assertTrue(any("unique" in error for error in protocol.validate_plan_events(duplicate)))

    def test_migration_preserves_legacy_chain_head(self):
        manifest = {
            "plan_protocol_version": protocol.PLAN_PROTOCOL_V1,
            "run_started_at": "2026-07-28T10:00:00Z",
            "plan_events": [],
        }
        original = protocol.append_plan_event(
            manifest["plan_events"],
            "candidate_linted",
            {"legacy": True},
            recorded_at="2026-07-29T10:00:00Z",
            event_id="00000000-0000-4000-8000-000000000001",
        )["event_sha256"]
        protocol.migrate_manifest_to_v2(
            manifest,
            recorded_at="2026-07-29T10:01:00Z",
            event_id="00000000-0000-4000-8000-000000000002",
        )
        self.assertEqual(manifest["plan_protocol_version"], protocol.PLAN_PROTOCOL_V2)
        self.assertEqual(manifest["plan_events"][-1]["previous_event_sha256"], original)
        self.assertEqual(
            manifest["plan_events"][-1]["payload"]["previous_event_sha256"], original
        )
        self.assertEqual(protocol.validate_plan_events(manifest["plan_events"]), [])

    def test_unsupported_downgrade_and_repeat_migration_fail_closed(self):
        self.assertTrue(
            protocol.validate_protocol_version({"plan_protocol_version": "plan-protocol/v99"})
        )
        self.assertTrue(
            protocol.validate_protocol_version(
                {"plan_protocol_version": protocol.PLAN_PROTOCOL_V1},
                expected=protocol.PLAN_PROTOCOL_V2,
            )
        )
        with self.assertRaises(protocol.PlanProtocolError):
            protocol.migrate_manifest_to_v2(
                {"plan_protocol_version": protocol.PLAN_PROTOCOL_V2, "plan_events": []}
            )

    def test_migration_cli_is_atomic_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "plan_protocol_version": protocol.PLAN_PROTOCOL_V1,
                        "run_started_at": "2026-07-28T10:00:00Z",
                        "plan_events": [],
                    }
                )
            )
            command = [
                sys.executable,
                str(ROOT / "scripts" / "migrate_plan_protocol.py"),
                str(path),
                "--recorded-at",
                "2026-07-29T10:00:00Z",
                "--event-id",
                "00000000-0000-4000-8000-000000000001",
            ]
            result = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(path.read_text())["plan_protocol_version"], protocol.PLAN_PROTOCOL_V2)
            before = path.read_text()
            failed = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(failed.returncode, 1)
            self.assertEqual(path.read_text(), before)


class AuditTests(unittest.TestCase):
    def test_high_finding_requires_fresh_verified_recheck(self):
        digest = "a" * 64
        preliminary = audit(
            "audit-1", "preliminary", digest, [finding("PA-001", "High", "open")]
        )
        final = audit("audit-2", "final_remote", digest, [])
        errors = protocol.validate_plan_audits(
            [preliminary, final], final_body_sha256=digest
        )
        self.assertTrue(any("remains unresolved" in error for error in errors))

        recheck = audit(
            "audit-2",
            "remediation_recheck",
            digest,
            [finding("PA-001", "High", "verified_fixed")],
            predecessor="audit-1",
            predecessor_findings=["PA-001"],
        )
        final = audit("audit-3", "final_remote", digest, [])
        self.assertEqual(
            protocol.validate_plan_audits(
                [preliminary, recheck, final], final_body_sha256=digest
            ),
            [],
        )

    def test_medium_must_be_fixed_or_owned(self):
        digest = "b" * 64
        accepted = finding(
            "PA-001",
            "Medium",
            "accepted",
            owner="maintainer",
            rationale="bounded compatibility cost",
        )
        final = audit("audit-1", "final_remote", digest, [accepted])
        self.assertEqual(
            protocol.validate_plan_audits([final], final_body_sha256=digest), []
        )
        accepted.pop("owner")
        final["result_sha256"] = audit_hash(final)
        self.assertTrue(
            any(
                "owner" in error
                for error in protocol.validate_plan_audits(
                    [final], final_body_sha256=digest
                )
            )
        )

    def test_rejects_stale_hash_mutation_and_agent_reuse(self):
        digest = "c" * 64
        final = audit("audit-1", "final_remote", "d" * 64, [])
        final["findings"].append(finding("PA-002", "Low", "open"))
        errors = protocol.validate_plan_audits(
            [final],
            final_body_sha256=digest,
            disallowed_agent_ids=[final["agent_id"]],
        )
        self.assertTrue(any("role/session separation" in error for error in errors))
        self.assertTrue(any("immutable audit" in error for error in errors))
        self.assertTrue(any("exact canonical remote" in error for error in errors))


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.tasks = protocol.parse_tasks(
            body(task(1), task(2, dependencies=(1,)))
        )
        self.parent = "https://github.com/example/project/issues/10"
        self.repository = "example/project"
        self.draft = protocol.freeze_graph_draft(
            self.parent, self.repository, self.tasks
        )
        self.capability = {
            "github_login": "operator",
            "github_account_id": "42",
            "repository": self.repository,
            "parent_issue_url": self.parent,
            "native_parent_supported": True,
            "blocking_supported": True,
            "readback_supported": True,
        }
        self.authorization = {
            "github_login": "operator",
            "github_account_id": "42",
            "repository": self.repository,
            "parent_issue_url": self.parent,
            "capability_receipt_sha256": protocol.sha256_json(self.capability),
            "draft_sha256": self.draft["draft_sha256"],
            "child_body_sha256s": [
                child["body_sha256"] for child in self.draft["children"]
            ],
            "edges": self.draft["edges"],
            "authorization_evidence": "User explicitly approved exact frozen draft",
            "authorized_at": "2026-07-29T10:00:00Z",
        }

    def remote_child(self, task_id: str) -> dict:
        child = next(value for value in self.draft["children"] if value["task_id"] == task_id)
        return {
            "task_id": task_id,
            "stable_marker": child["stable_marker"],
            "title": child["title"],
            "body_sha256": child["body_sha256"],
            "parent_issue_url": self.parent,
        }

    def test_authorization_exact_binding_and_identity_drift(self):
        kwargs = {
            "current_login": "operator",
            "current_account_id": "42",
            "current_repository": self.repository,
            "current_parent_issue_url": self.parent,
            "capability_receipt": self.capability,
        }
        self.assertEqual(
            protocol.verify_graph_authorization(self.authorization, self.draft, **kwargs),
            [],
        )
        kwargs["current_login"] = "other"
        self.assertTrue(
            any(
                "github_login" in error
                for error in protocol.verify_graph_authorization(
                    self.authorization, self.draft, **kwargs
                )
            )
        )

    def test_exact_subset_resume_and_conflicts(self):
        empty = protocol.reconcile_graph_state(
            self.draft, {"children": [], "edges": []}
        )
        self.assertEqual(empty["classification"], "AUTHORIZED_MISSING")
        subset = protocol.reconcile_graph_state(
            self.draft, {"children": [self.remote_child("T-001")], "edges": []}
        )
        self.assertEqual(subset["classification"], "AUTHORIZED_MISSING")
        exact = protocol.reconcile_graph_state(
            self.draft,
            {
                "children": [self.remote_child("T-001"), self.remote_child("T-002")],
                "edges": self.draft["edges"],
            },
        )
        self.assertEqual(exact["classification"], "EXACT_MATCH")

        drift = self.remote_child("T-001")
        drift["body_sha256"] = "f" * 64
        conflict_cases = (
            {"children": [drift], "edges": []},
            {"children": [self.remote_child("T-001"), {"task_id": "T-999"}], "edges": []},
            {
                "children": [self.remote_child("T-001")],
                "edges": [{"blocked": "T-001", "blocked_by": "T-002"}],
            },
        )
        for state in conflict_cases:
            with self.subTest(state=state):
                self.assertEqual(
                    protocol.reconcile_graph_state(self.draft, state)["classification"],
                    "CONFLICT",
                )

    def test_final_graph_requires_verified_action_order(self):
        remote = {
            "children": [self.remote_child("T-001"), self.remote_child("T-002")],
            "edges": self.draft["edges"],
        }
        records = []
        for kind, key in (
            ("child", "T-001"),
            ("child", "T-002"),
            ("edge", "T-002<-T-001"),
        ):
            records.extend(
                (
                    {"kind": kind, "key": key, "status": "attempted"},
                    {"kind": kind, "key": key, "status": "verified"},
                )
            )
        self.assertEqual(protocol.verify_final_graph(self.draft, remote, records), [])
        self.assertTrue(
            any(
                "before attempted" in error
                for error in protocol.verify_final_graph(
                    self.draft,
                    remote,
                    [{"kind": "child", "key": "T-001", "status": "verified"}],
                )
            )
        )


class CliTests(unittest.TestCase):
    def test_lint_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.md"
            path.write_text(body(task(1)))
            command = [sys.executable, str(ROOT / "scripts" / "plan_lint.py"), str(path)]
            passed = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(passed.returncode, 0, passed.stdout)
            path.write_text(body(task(1).replace("Verification:", "Verify:")))
            failed = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(failed.returncode, 1)
        self.assertEqual(json.loads(failed.stdout)["status"], "FAIL")


class PrivacyTests(unittest.TestCase):
    def test_detects_secrets_and_contact_data_without_matching_policy_words(self):
        violations = protocol.privacy_violations(
            {
                "safe": "Do not publish tokens or credentials.",
                "token": "ghp_abcdefghijklmnopqrstuvwxyz123456",
                "contact": "person@example.com",
            }
        )
        self.assertEqual(len(violations), 2)
        self.assertTrue(any("$.token" in value for value in violations))
        self.assertTrue(any("$.contact" in value for value in violations))


if __name__ == "__main__":
    unittest.main()
