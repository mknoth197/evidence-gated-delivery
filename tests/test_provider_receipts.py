from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts import provider_receipts
from scripts import validate_run as validator


class ProviderReceiptTests(unittest.TestCase):
    def receipt(self, root: Path) -> tuple[dict[str, object], dict[str, object]]:
        transcript = root / "claude-transcript.jsonl"
        transcript.write_text('{"event":"subagent_stop"}\n')
        result = "PASS E1 provider evidence"
        values: dict[str, object] = {
            "provider": "claude_code",
            "provider_version": "1.0",
            "parent_session_id": "parent-1",
            "child_session_id": "child-1",
            "delegated_role": "Execution auditor phase: research",
            "started_at": "2026-07-31T15:00:00Z",
            "completed_at": "2026-07-31T15:01:00Z",
            "transcript_path": str(transcript),
            "transcript_sha256": hashlib.sha256(transcript.read_bytes()).hexdigest(),
            "final_result": result,
            "final_result_sha256": hashlib.sha256(result.encode()).hexdigest(),
        }
        binding = "\n".join(str(values[key]) for key in ("provider", "provider_version", "parent_session_id", "child_session_id", "delegated_role", "started_at", "completed_at", "transcript_sha256", "final_result_sha256"))
        values["parent_child_binding_sha256"] = hashlib.sha256(binding.encode()).hexdigest()
        receipt_path = root / "receipt.json"
        receipt_path.write_text(json.dumps(values))
        data = {"parent_thread_id": "parent-1", "provider_context": {"provider": "claude_code", "provider_version": "1.0", "allowed_transcript_roots": [str(root)]}}
        audit = {"agent_id": "child-1", "role_marker": "Execution auditor phase: research", "result": result, "provider_receipt_path": str(receipt_path)}
        return data, audit

    def test_accepts_complete_allowlisted_claude_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            data, audit = self.receipt(Path(temporary))
            evidence, error = provider_receipts.provider_delegated_audit_evidence(data, audit)
        self.assertIsNone(error)
        self.assertEqual(evidence["final_message"], audit["result"])

    def test_rejects_unallowlisted_transcript_and_tampered_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data, audit = self.receipt(root)
            data["provider_context"]["allowed_transcript_roots"] = [str(root / "other")]
            _, error = provider_receipts.provider_delegated_audit_evidence(data, audit)
            self.assertIn("not allowlisted", error)
            data, audit = self.receipt(root)
            receipt_path = Path(audit["provider_receipt_path"])
            receipt = json.loads(receipt_path.read_text())
            receipt["final_result"] = "PASS tampered"
            receipt_path.write_text(json.dumps(receipt))
            _, error = provider_receipts.provider_delegated_audit_evidence(data, audit)
            self.assertIn("final result mismatch", error)

    def test_trace_validator_accepts_only_the_normalized_provider_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            data, audit = self.receipt(Path(temporary))
            audit.update(
                {
                    "phase": "research",
                    "receipt_kind": "provider_delegated",
                    "status": "completed",
                    "verdict": "PASS",
                    "result_sha256": hashlib.sha256(audit["result"].encode()).hexdigest(),
                    "verified_event_ids": ["E1"],
                }
            )
            data.update(
                {
                    "run_started_at": "2026-07-31T14:00:00Z",
                    "phase_timeline": {"research_completed_at": "2026-07-31T15:02:00Z"},
                    "trace_audits": [audit],
                }
            )
            errors: list[str] = []
            validator.validate_trace_audit(data, "research", errors)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
