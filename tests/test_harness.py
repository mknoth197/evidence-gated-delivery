from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight = load_module("preflight_plan", SKILL_ROOT / "scripts" / "preflight_plan.py")
validator = load_module("validate_run", SKILL_ROOT / "scripts" / "validate_run.py")


class FakeHeaders:
    def __init__(self, content_type: str):
        self.content_type = content_type

    def get_content_type(self) -> str:
        return self.content_type


class FakeResponse:
    def __init__(self, payload: bytes, content_type: str = "image/png"):
        self.payload = payload
        self.headers = FakeHeaders(content_type)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return self.payload


class PublicationTests(unittest.TestCase):
    def transition_data(self, confidence=8, stops=None, findings=None):
        receipt_sha = "a" * 64
        return {
            "automation_policy": {
                "default_mode": "autonomous",
                "auto_transition_min_confidence": 8,
                "stop_before_phases": stops or [],
                "released_stop_gates": [],
                "hard_stop_categories": ["protected_external_write"],
            },
            "phase_receipt_bindings": {"research": {"receipt_sha256": receipt_sha}},
            "phase_transition_judgments": [{
                "phase": "research",
                "successor_phase": "plan",
                "agent_id": "transition-judge",
                "status": "pass",
                "recommendation": "proceed",
                "confidence": confidence,
                "technical_accuracy_score": 3,
                "evidence_ids": ["E1"],
                "blocking_findings": findings or [],
                "completed_at": "2026-07-25T12:00:00Z",
                "phase_receipt_sha256": receipt_sha,
                "result_sha256": "b" * 64,
            }],
            "phase_retrospectives": [],
            "trace_audits": [],
            "unresolved_hard_stops": [],
            "automation_decisions": [{
                "from_phase": "research",
                "to_phase": "plan",
                "decision": "auto_proceed",
                "judge_receipt_sha256": "b" * 64,
                "decided_at": "2026-07-25T12:01:00Z",
            }],
        }

    def test_transition_judge_allows_exact_threshold(self):
        errors: list[str] = []
        validator.validate_transition_gate(self.transition_data(), "plan", errors)
        self.assertEqual(errors, [])

    def test_transition_judge_rejects_below_threshold_and_high_finding(self):
        errors: list[str] = []
        validator.validate_transition_gate(self.transition_data(confidence=7), "plan", errors)
        self.assertTrue(any("integer from 8" in error for error in errors))
        errors = []
        validator.validate_transition_gate(
            self.transition_data(findings=[{"severity": "high", "claim": "missing test"}]),
            "plan",
            errors,
        )
        self.assertTrue(any("high or critical" in error for error in errors))

    def test_human_stop_gate_overrides_high_confidence(self):
        errors: list[str] = []
        validator.validate_transition_gate(self.transition_data(stops=["plan"]), "plan", errors)
        self.assertTrue(any("human stop gate" in error for error in errors))

    def test_rejects_hostname_spoof(self):
        self.assertFalse(preflight.approved_url("https://evil.example/openai.site/image.png", []))

    def test_accepts_explicit_custom_host_in_both_gates(self):
        url = "https://assets.example.test/mockup.png"
        self.assertTrue(preflight.approved_url(url, ["assets.example.test"]))
        self.assertTrue(validator.durable_image_url(url, ["assets.example.test"]))

    def test_remote_image_hashes_exact_bytes(self):
        payload = b"\x89PNG\r\n\x1a\nexact"
        with patch.object(preflight, "urlopen", return_value=FakeResponse(payload)):
            digest, error = preflight.remote_image_sha256("https://openai.site/mockup.png")
        self.assertIsNone(error)
        self.assertEqual(digest, hashlib.sha256(payload).hexdigest())

    def test_remote_image_rejects_non_image(self):
        with patch.object(
            validator,
            "urlopen",
            return_value=FakeResponse(b"<html></html>", "text/html"),
        ):
            digest, error = validator.remote_image_sha256("https://openai.site/mockup")
        self.assertIsNone(digest)
        self.assertIn("not image", error)

    def test_plan_identity_gate_rejects_missing_live_shell_and_actions(self):
        errors: list[str] = []
        validator.validate_plan_identity_and_evidence(
            {
                "research_issue_url": "https://github.com/org/repo/issues/1",
                "implementation_issue_url": "https://github.com/org/repo/issues/2",
            },
            errors,
        )
        self.assertTrue(any("initiative_identity is required" in error for error in errors))
        self.assertTrue(any("visual_grounding" in error for error in errors))
        self.assertTrue(any("external_actions" in error for error in errors))

    def test_retrospective_gate_rejects_missing_research_learning(self):
        errors: list[str] = []
        validator.validate_retrospective_gate({}, "plan", errors)
        self.assertTrue(any("phase_retrospectives" in error for error in errors))

    def test_retrospective_gate_accepts_fixed_high_score(self):
        scorecard = {key: 4 for key in validator.RETROSPECTIVE_RUBRIC}
        evidence = {key: ["verified artifact"] for key in validator.RETROSPECTIVE_RUBRIC}
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = Path(directory) / "research.json"
            receipt_path.write_text(json.dumps({
                "status": "VALID",
                "phase": "research",
                "validated_at": "2026-07-23T12:30:00Z",
            }))
            errors: list[str] = []
            validator.validate_retrospective_gate(
                {
                    "phase_timeline": {
                        "research_completed_at": "2026-07-23T12:30:00Z"
                    },
                    "phase_receipt_bindings": {
                        "research": {
                            "status": "VALID",
                            "validated_at": "2026-07-23T12:30:00Z",
                            "receipt_path": str(receipt_path),
                            "receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                        }
                    },
                    "phase_retrospectives": [{"phase": "research", "agent_id": "retrospective-agent", "status": "completed", "scorecard": scorecard, "evidence": evidence, "total": 100, "degradation_detected": False, "remediation_actions": [], "remediation_rechecked": False}],
                },
                "plan",
                errors,
            )
        self.assertEqual(errors, [])

    def test_retrospective_gate_rejects_unbound_completion_time(self):
        scorecard = {key: 4 for key in validator.RETROSPECTIVE_RUBRIC}
        evidence = {key: ["verified artifact"] for key in validator.RETROSPECTIVE_RUBRIC}
        errors: list[str] = []
        validator.validate_retrospective_gate(
            {
                "phase_timeline": {
                    "research_completed_at": "2026-07-23T12:30:00Z"
                },
                "phase_retrospectives": [{"phase": "research", "agent_id": "retrospective-agent", "status": "completed", "scorecard": scorecard, "evidence": evidence, "total": 100, "degradation_detected": False, "remediation_actions": [], "remediation_rechecked": False}],
            },
            "plan",
            errors,
        )
        self.assertTrue(any("phase_receipt_bindings" in error for error in errors))


class AuditorAuthenticationTests(unittest.TestCase):
    parent_thread_id = "019f0000-0000-7000-8000-000000000001"

    def write_session(self, root: Path, agent_id: str, prompt: str, result: str):
        session_dir = root / "sessions" / "2026" / "07" / "23"
        session_dir.mkdir(parents=True)
        path = session_dir / f"rollout-{agent_id}.jsonl"
        records = [
            {
                "type": "session_meta",
                "payload": {
                    "id": agent_id,
                    "timestamp": "2026-07-23T12:05:00Z",
                    "thread_source": "subagent",
                    "source": {
                        "subagent": {
                            "thread_spawn": {
                                "parent_thread_id": self.parent_thread_id,
                                "depth": 1,
                            }
                        }
                    },
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-07-23T12:10:00Z",
                "payload": {
                    "type": "task_complete",
                    "last_agent_message": result,
                },
            },
        ]
        path.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    def test_authenticates_completed_auditor_session(self):
        agent_id = "019f0000-0000-7000-8000-000000000099"
        result = "PASS plan evidence-id-1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_session(
                root,
                agent_id,
                "Execution auditor phase: plan",
                result,
            )
            audit = {
                "phase": "plan",
                "agent_id": agent_id,
                "status": "completed",
                "verdict": "PASS",
                "result": "PASS",
                "result_sha256": hashlib.sha256(result.encode()).hexdigest(),
                "verified_event_ids": ["evidence-id-1"],
            }
            with patch.dict(os.environ, {"CODEX_HOME": directory}):
                errors: list[str] = []
                validator.validate_trace_audit(
                    {
                        "run_started_at": "2026-07-23T12:00:00Z",
                        "phase_timeline": {
                            "plan_completed_at": "2026-07-23T12:30:00Z"
                        },
                        "parent_thread_id": self.parent_thread_id,
                        "trace_audits": [audit],
                    },
                    "plan",
                    errors,
                )
        self.assertEqual(errors, [])

    def test_rejects_fabricated_auditor_without_session(self):
        audit = {
            "phase": "plan",
            "agent_id": "019f0000-0000-7000-8000-000000000098",
            "status": "completed",
            "verdict": "PASS",
            "result": "PASS",
            "result_sha256": "0" * 64,
            "verified_event_ids": ["fake"],
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"CODEX_HOME": directory}):
                errors: list[str] = []
                validator.validate_trace_audit(
                    {
                        "run_started_at": "2026-07-23T12:00:00Z",
                        "parent_thread_id": self.parent_thread_id,
                        "trace_audits": [audit],
                    },
                    "plan",
                    errors,
                )
        self.assertTrue(any("session verification failed" in error for error in errors))

    def test_accepts_child_metadata_when_parent_metadata_is_replayed(self):
        agent_id = "019f0000-0000-7000-8000-000000000095"
        result = "PASS plan evidence-id-1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_session(
                root,
                agent_id,
                "Execution auditor phase: plan",
                result,
            )
            session = next((root / "sessions").rglob("*.jsonl"))
            with session.open("a") as output:
                output.write(json.dumps({
                    "type": "session_meta",
                    "payload": {
                        "id": self.parent_thread_id,
                        "timestamp": "2026-07-23T12:00:00Z",
                        "thread_source": "cli",
                        "source": {},
                    },
                }) + "\n")
            audit = {
                "phase": "plan",
                "agent_id": agent_id,
                "status": "completed",
                "verdict": "PASS",
                "result": "PASS",
                "result_sha256": hashlib.sha256(result.encode()).hexdigest(),
                "verified_event_ids": ["evidence-id-1"],
            }
            with patch.dict(os.environ, {"CODEX_HOME": directory}):
                errors: list[str] = []
                validator.validate_trace_audit(
                    {
                        "run_started_at": "2026-07-23T12:00:00Z",
                        "phase_timeline": {
                            "plan_completed_at": "2026-07-23T12:30:00Z"
                        },
                        "parent_thread_id": self.parent_thread_id,
                        "trace_audits": [audit],
                    },
                    "plan",
                    errors,
                )
        self.assertEqual(errors, [])

    def test_rejects_phase_completion_before_auditor_completion(self):
        agent_id = "019f0000-0000-7000-8000-000000000096"
        result = "PASS plan evidence-id-1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_session(
                root,
                agent_id,
                "Execution auditor phase: plan",
                result,
            )
            audit = {
                "phase": "plan",
                "agent_id": agent_id,
                "status": "completed",
                "verdict": "PASS",
                "result": "PASS",
                "result_sha256": hashlib.sha256(result.encode()).hexdigest(),
                "verified_event_ids": ["evidence-id-1"],
            }
            with patch.dict(os.environ, {"CODEX_HOME": directory}):
                errors: list[str] = []
                validator.validate_trace_audit(
                    {
                        "run_started_at": "2026-07-23T12:00:00Z",
                        "phase_timeline": {
                            "plan_completed_at": "2026-07-23T12:09:59Z"
                        },
                        "parent_thread_id": self.parent_thread_id,
                        "trace_audits": [audit],
                    },
                    "plan",
                    errors,
                )
        self.assertTrue(any("authenticated auditor completion" in error for error in errors))

    def test_rejects_non_subagent_session(self):
        agent_id = "019f0000-0000-7000-8000-000000000097"
        result = "PASS plan evidence-id-1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_session(root, agent_id, "Execution auditor phase: plan", result)
            session = next((root / "sessions").rglob("*.jsonl"))
            records = [json.loads(line) for line in session.read_text().splitlines()]
            records[0]["payload"]["thread_source"] = "cli"
            records[0]["payload"]["source"] = {}
            session.write_text("\n".join(json.dumps(record) for record in records) + "\n")
            audit = {
                "phase": "plan",
                "agent_id": agent_id,
                "status": "completed",
                "verdict": "PASS",
                "result": "PASS",
                "result_sha256": hashlib.sha256(result.encode()).hexdigest(),
                "verified_event_ids": ["evidence-id-1"],
            }
            with patch.dict(os.environ, {"CODEX_HOME": directory}):
                errors: list[str] = []
                validator.validate_trace_audit(
                    {
                        "run_started_at": "2026-07-23T12:00:00Z",
                        "parent_thread_id": self.parent_thread_id,
                        "trace_audits": [audit],
                    },
                    "plan",
                    errors,
                )
        self.assertTrue(any("depth-one Codex subagent" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
