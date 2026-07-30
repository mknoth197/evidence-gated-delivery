from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import validate_run as validator


class NestedCollaborationAncestryTests(unittest.TestCase):
    parent_id = "019f0000-0000-7000-8000-000000000001"
    intermediary_id = "019f0000-0000-7000-8000-000000000093"
    agent_id = "019f0000-0000-7000-8000-000000000094"
    result = "PASS plan evidence-id-1"
    nested_path = "/root/coordinator/test_coverage_reviewer"

    def write_sessions(self, root: Path) -> None:
        directory = root / "sessions" / "2026" / "07" / "29"
        directory.mkdir(parents=True)
        child = [
            {
                "type": "session_meta",
                "payload": {
                    "id": self.agent_id,
                    "thread_source": "subagent",
                    "source": {
                        "subagent": {
                            "thread_spawn": {
                                "parent_thread_id": self.intermediary_id,
                                "depth": 2,
                                "agent_path": self.nested_path,
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
        intermediary = [
            {
                "type": "session_meta",
                "payload": {
                    "id": self.intermediary_id,
                    "thread_source": "subagent",
                    "source": {
                        "subagent": {
                            "thread_spawn": {
                                "parent_thread_id": self.parent_id,
                                "depth": 1,
                                "agent_path": "/root/coordinator",
                            }
                        }
                    },
                },
            },
            self.spawn_call("call-nested", "test_coverage_reviewer"),
            self.started_event("call-nested", self.agent_id, self.nested_path),
            self.callback(self.nested_path, "/root/coordinator", self.result),
            {
                "type": "event_msg",
                "timestamp": "2026-07-29T12:11:00Z",
                "payload": {
                    "type": "task_complete",
                    "last_agent_message": "PASS coordinator",
                },
            },
        ]
        root_parent = [
            {
                "type": "session_meta",
                "payload": {"id": self.parent_id, "thread_source": "user"},
            },
            self.spawn_call("call-coordinator", "coordinator"),
            self.started_event(
                "call-coordinator", self.intermediary_id, "/root/coordinator"
            ),
            self.callback("/root/coordinator", "/root", "PASS coordinator"),
        ]
        for identifier, records in (
            (self.agent_id, child),
            (self.intermediary_id, intermediary),
            (self.parent_id, root_parent),
        ):
            (directory / f"rollout-{identifier}.jsonl").write_text(
                "\n".join(json.dumps(item) for item in records) + "\n"
            )

    @staticmethod
    def spawn_call(call_id: str, task_name: str) -> dict[str, object]:
        return {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "namespace": "collaboration",
                "name": "spawn_agent",
                "call_id": call_id,
                "arguments": json.dumps(
                    {"task_name": task_name, "message": "gAAAAABencrypted"}
                ),
            },
        }

    @staticmethod
    def started_event(
        call_id: str, agent_id: str, agent_path: str
    ) -> dict[str, object]:
        return {
            "type": "event_msg",
            "timestamp": "2026-07-29T12:05:00Z",
            "payload": {
                "type": "sub_agent_activity",
                "event_id": call_id,
                "agent_thread_id": agent_id,
                "agent_path": agent_path,
                "kind": "started",
            },
        }

    @staticmethod
    def callback(author: str, recipient: str, result: str) -> dict[str, object]:
        return {
            "type": "response_item",
            "payload": {
                "type": "agent_message",
                "author": author,
                "recipient": recipient,
                "content": [{"type": "input_text", "text": f"Payload:\n{result}"}],
            },
        }

    def reviewer(self) -> dict[str, object]:
        return {
            "receipt_kind": "collaboration_delegated",
            "agent_id": self.agent_id,
            "agent_path": self.nested_path,
            "status": "completed",
            "result": self.result,
            "result_sha256": hashlib.sha256(self.result.encode()).hexdigest(),
            "started_at": "2026-07-29T12:05:00Z",
            "completed_at": "2026-07-29T12:10:00Z",
        }

    def validate(self) -> tuple[str | None, list[str]]:
        errors: list[str] = []
        reviewer_id = validator.validate_reviewer(
            {"parent_thread_id": self.parent_id},
            self.reviewer(),
            "test_reviewer",
            errors,
            expected_marker="Test-Coverage Reviewer",
        )
        return reviewer_id, errors

    def test_authenticates_every_uuid_backed_ancestry_edge(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.write_sessions(Path(temporary))
            with patch.dict(os.environ, {"CODEX_HOME": temporary}):
                reviewer_id, errors = self.validate()
        self.assertEqual(reviewer_id, self.agent_id)
        self.assertEqual(errors, [])

    def test_rejects_spoofed_intermediary_depth(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.write_sessions(Path(temporary))
            intermediary_path = next(
                (Path(temporary) / "sessions").rglob(f"*{self.intermediary_id}.jsonl")
            )
            records = [
                json.loads(line) for line in intermediary_path.read_text().splitlines()
            ]
            records[0]["payload"]["source"]["subagent"]["thread_spawn"]["depth"] = 99
            intermediary_path.write_text(
                "\n".join(json.dumps(item) for item in records) + "\n"
            )
            with patch.dict(os.environ, {"CODEX_HOME": temporary}):
                reviewer_id, errors = self.validate()
        self.assertIsNone(reviewer_id)
        self.assertTrue(any("path or depth mismatch" in error for error in errors), errors)

    def test_rejects_missing_root_edge(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.write_sessions(Path(temporary))
            root_path = next(
                (Path(temporary) / "sessions").rglob(f"*{self.parent_id}.jsonl")
            )
            root_path.unlink()
            with patch.dict(os.environ, {"CODEX_HOME": temporary}):
                reviewer_id, errors = self.validate()
        self.assertIsNone(reviewer_id)
        self.assertTrue(any("parent session" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
