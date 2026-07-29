import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import graph_transaction as transaction
import plan_protocol as protocol


def task(task_id, dependency=()):
    return {
        "task_id": task_id,
        "title": f"Task {task_id}",
        "body": f"- [ ] **{task_id} — Task.**",
        "body_sha256": protocol.issue_body_sha256(
            f"- [ ] **{task_id} — Task.**"
        ),
        "owner_lane": "lane",
        "depends_on": list(dependency),
    }


class GraphTransactionTests(unittest.TestCase):
    def setUp(self):
        self.parent = "https://github.com/o/r/issues/1"
        self.draft = protocol.freeze_graph_draft(
            self.parent, "o/r", [task("T-001"), task("T-002", ("T-001",))]
        )
        self.empty = {"children": [], "edges": []}

    def test_plans_only_authorized_missing_subset(self):
        self.assertEqual(
            transaction.planned_actions(self.draft, self.empty),
            [
                {"kind": "create_child", "key": "T-001"},
                {"kind": "create_child", "key": "T-002"},
                {"kind": "add_blocked_by", "key": "T-002<-T-001"},
            ],
        )

    def test_conflict_blocks_before_write(self):
        conflicting = {
            "children": [
                {
                    "task_id": "T-001",
                    "stable_marker": "wrong",
                    "title": "wrong",
                    "body_sha256": "0" * 64,
                    "parent_issue_url": self.parent,
                }
            ],
            "edges": [],
        }
        with self.assertRaises(protocol.PlanProtocolError):
            transaction.planned_actions(self.draft, conflicting)

    def test_guard_rejects_identity_and_capability_drift(self):
        capability = {
            "github_login": "me",
            "github_account_id": "1",
            "repository": "o/r",
            "parent_issue_url": self.parent,
            "native_parent_supported": True,
            "blocking_supported": True,
            "readback_supported": True,
        }
        authorization = {
            "github_login": "me",
            "github_account_id": "1",
            "repository": "o/r",
            "parent_issue_url": self.parent,
            "draft_sha256": self.draft["draft_sha256"],
            "capability_receipt_sha256": protocol.sha256_json(capability),
            "child_body_sha256s": [
                child["body_sha256"] for child in self.draft["children"]
            ],
            "edges": self.draft["edges"],
            "authorization_evidence": "approved exact graph",
            "authorized_at": "2026-07-29T12:00:00Z",
        }
        wrong_identity = transaction.authorization_guard(
            authorization,
            self.draft,
            capability,
            live_evidence=lambda: {
                "github_login": "other",
                "github_account_id": "2",
                "repository": "o/r",
                "parent_issue_url": self.parent,
                "capability_receipt": capability,
            },
        )
        self.assertTrue(wrong_identity())
        stale = copy.deepcopy(capability)
        stale["blocking_supported"] = False
        wrong_capability = transaction.authorization_guard(
            authorization,
            self.draft,
            stale,
            live_evidence=lambda: {
                "github_login": "me",
                "github_account_id": "1",
                "repository": "o/r",
                "parent_issue_url": self.parent,
                "capability_receipt": stale,
            },
        )
        self.assertTrue(wrong_capability())

    def test_attempt_precedes_write_and_crash_stops(self):
        state = {"children": [], "edges": []}
        calls = []

        def readback():
            return copy.deepcopy(state)

        def runner(action):
            calls.append((action, "called"))
            return {"ok": False, "error": "simulated"}

        with self.assertRaises(protocol.PlanProtocolError):
            transaction.execute_transaction(
                self.draft,
                state,
                guard=lambda: [],
                runner=runner,
                readback=readback,
                recorder=lambda record: None,
            )
        self.assertEqual(len(calls), 1)

    def test_rechecks_guard_before_every_write(self):
        state = {"children": [], "edges": []}
        checks = 0

        def guard():
            nonlocal checks
            checks += 1
            return [] if checks == 1 else ["authorization became stale"]

        def readback():
            return copy.deepcopy(state)

        def runner(action):
            child = self.draft["children"][0]
            state["children"].append(
                {
                    "task_id": child["task_id"],
                    "stable_marker": child["stable_marker"],
                    "title": child["title"],
                    "body_sha256": child["body_sha256"],
                    "parent_issue_url": self.parent,
                }
            )
            return {"ok": True}

        with self.assertRaises(protocol.PlanProtocolError):
            transaction.execute_transaction(
                self.draft,
                state,
                guard=guard,
                runner=runner,
                readback=readback,
                recorder=lambda record: None,
            )
        self.assertEqual(checks, 2)

    def test_recorder_flushes_attempt_and_blocked_when_runner_raises(self):
        state = {"children": [], "edges": []}
        durable = []
        ordering = []

        def recorder(record):
            durable.append(copy.deepcopy(record))
            ordering.append(record["status"])

        def runner(_action):
            ordering.append("runner")
            raise RuntimeError("injected crash")

        with self.assertRaises(protocol.PlanProtocolError):
            transaction.execute_transaction(
                self.draft,
                state,
                guard=lambda: [],
                runner=runner,
                readback=lambda: copy.deepcopy(state),
                recorder=recorder,
            )
        self.assertEqual(ordering, ["attempted", "runner", "blocked"])
        self.assertEqual(
            [record["status"] for record in durable], ["attempted", "blocked"]
        )

    def test_successful_mutation_with_stale_readback_records_blocked_evidence(self):
        state = {"children": [], "edges": []}
        durable = []

        with self.assertRaisesRegex(
            protocol.PlanProtocolError, "lacks verified readback"
        ):
            transaction.execute_transaction(
                self.draft,
                state,
                guard=lambda: [],
                runner=lambda _action: {
                    "ok": True,
                    "mutation_id": "safe-id",
                    "detail": "ghp_abcdefghijklmnopqrstuvwxyz123456",
                    "ghp_abcdefghijklmnopqrstuvwxyz654321": "secret in key",
                },
                readback=lambda: copy.deepcopy(state),
                recorder=lambda record: durable.append(copy.deepcopy(record)),
            )

        self.assertEqual(
            [record["status"] for record in durable], ["attempted", "blocked"]
        )
        blocked = durable[-1]
        self.assertEqual(blocked["result"]["reason"], "readback_unverified")
        self.assertEqual(blocked["result"]["mutation"]["mutation_id"], "safe-id")
        self.assertEqual(blocked["result"]["mutation"]["detail"], "[REDACTED]")
        self.assertEqual(blocked["result"]["readback"], state)
        self.assertNotIn("ghp_", json.dumps(blocked))

    def test_verified_record_redacts_sensitive_dictionary_keys(self):
        draft = protocol.freeze_graph_draft(
            self.parent, "o/r", [task("T-001")]
        )
        state = {"children": [], "edges": []}
        durable = []

        def runner(_action):
            child = draft["children"][0]
            state["children"].append(
                {
                    "task_id": child["task_id"],
                    "stable_marker": child["stable_marker"],
                    "title": child["title"],
                    "body_sha256": child["body_sha256"],
                    "parent_issue_url": self.parent,
                }
            )
            return {
                "ok": True,
                "mutation_id": "safe-id",
                "ghp_abcdefghijklmnopqrstuvwxyz654321": "secret in key",
            }

        records, _current = transaction.execute_transaction(
            draft,
            state,
            guard=lambda: [],
            runner=runner,
            readback=lambda: copy.deepcopy(state),
            recorder=lambda record: durable.append(copy.deepcopy(record)),
        )

        self.assertEqual([record["status"] for record in records], ["attempted", "verified"])
        self.assertNotIn("ghp_", json.dumps(records))
        self.assertNotIn("ghp_", json.dumps(durable))
        self.assertIn("[REDACTED_KEY_3]", records[-1]["result"])

    def test_successful_mutation_with_readback_exception_records_blocked_evidence(self):
        state = {"children": [], "edges": []}
        durable = []
        read_count = 0

        def readback():
            nonlocal read_count
            read_count += 1
            if read_count == 1:
                return copy.deepcopy(state)
            raise RuntimeError(
                "readback failed with ghp_abcdefghijklmnopqrstuvwxyz123456"
            )

        with self.assertRaisesRegex(
            protocol.PlanProtocolError, "readback raised after successful write"
        ):
            transaction.execute_transaction(
                self.draft,
                state,
                guard=lambda: [],
                runner=lambda _action: {"ok": True, "mutation_id": "safe-id"},
                readback=readback,
                recorder=lambda record: durable.append(copy.deepcopy(record)),
            )

        self.assertEqual(
            [record["status"] for record in durable], ["attempted", "blocked"]
        )
        blocked = durable[-1]
        self.assertEqual(blocked["result"]["reason"], "readback_exception")
        self.assertEqual(blocked["result"]["mutation"]["mutation_id"], "safe-id")
        self.assertEqual(
            blocked["result"]["error"],
            {"type": "RuntimeError", "message": "[REDACTED]"},
        )
        self.assertNotIn("ghp_", json.dumps(blocked))

    def test_successful_mutation_with_missing_readback_records_returned_value(self):
        state = {"children": [], "edges": []}
        durable = []
        read_count = 0

        def readback():
            nonlocal read_count
            read_count += 1
            return copy.deepcopy(state) if read_count == 1 else None

        with self.assertRaisesRegex(
            protocol.PlanProtocolError, "readback was invalid after successful write"
        ):
            transaction.execute_transaction(
                self.draft,
                state,
                guard=lambda: [],
                runner=lambda _action: {"ok": True, "mutation_id": "safe-id"},
                readback=readback,
                recorder=lambda record: durable.append(copy.deepcopy(record)),
            )

        self.assertEqual(
            [record["status"] for record in durable], ["attempted", "blocked"]
        )
        blocked = durable[-1]
        self.assertEqual(blocked["result"]["reason"], "readback_invalid")
        self.assertIsNone(blocked["result"]["readback"])
        self.assertEqual(blocked["result"]["error"]["type"], "PlanProtocolError")

    def test_live_guard_refreshes_identity_every_time(self):
        capability = {
            "github_login": "me",
            "github_account_id": "1",
            "repository": "o/r",
            "parent_issue_url": self.parent,
            "native_parent_supported": True,
            "blocking_supported": True,
            "readback_supported": True,
        }
        authorization = {
            "github_login": "me",
            "github_account_id": "1",
            "repository": "o/r",
            "parent_issue_url": self.parent,
            "draft_sha256": self.draft["draft_sha256"],
            "capability_receipt_sha256": protocol.sha256_json(capability),
            "child_body_sha256s": [
                child["body_sha256"] for child in self.draft["children"]
            ],
            "edges": self.draft["edges"],
            "authorization_evidence": "approved exact graph",
            "authorized_at": "2026-07-29T12:00:00Z",
        }
        reads = 0

        def live():
            nonlocal reads
            reads += 1
            return {
                "github_login": "me" if reads == 1 else "other",
                "github_account_id": "1",
                "repository": "o/r",
                "parent_issue_url": self.parent,
                "capability_receipt": capability,
            }

        guard = transaction.authorization_guard(
            authorization, self.draft, capability, live_evidence=live
        )
        self.assertEqual(guard(), [])
        self.assertTrue(guard())
        self.assertEqual(reads, 2)


if __name__ == "__main__":
    unittest.main()
