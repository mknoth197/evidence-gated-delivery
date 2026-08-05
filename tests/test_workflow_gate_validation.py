from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from workflow_gate_validation import validate_gate_economics


def gate(gate_id: str = "execution-audit", **updates):
    value = {
        "schema_version": "gate-economics/v1",
        "gate_id": gate_id,
        "name": "Execution audit",
        "applicability_predicate": "assurance == heavy",
        "applicable": True,
        "failure_class": "unverified_external_action",
        "expected_latency_ms": 1000,
        "actual_latency_ms": 900,
        "cost_proxy": "one delegated review",
        "finding": {"status": "no_finding", "finding_ids": []},
        "remediation": None,
        "raw_denominator": 1,
        "duplicate_finding_count": 0,
        "duplicate_finding_rate": 0.0,
        "downstream_outcome": "INSUFFICIENT_EVIDENCE",
        "status": "active",
    }
    value.update(updates)
    return value


class WorkflowGateEconomicsTests(unittest.TestCase):
    def test_valid_no_finding_preserves_insufficient_evidence(self):
        self.assertEqual(
            validate_gate_economics([gate()], required_gate_ids=("execution-audit",)),
            {"errors": [], "diagnostics": []},
        )

    def test_raw_duplicate_rate_is_recomputed(self):
        result = validate_gate_economics([gate(raw_denominator=2, duplicate_finding_count=1, duplicate_finding_rate=0.2)])
        self.assertTrue(any("duplicate_finding_rate" in error for error in result["errors"]))

    def test_overlap_is_diagnostic_not_retirement_authority(self):
        result = validate_gate_economics([gate(), gate("transition-judge")])
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["diagnostics"]), 1)

    def test_required_gate_cannot_be_retired(self):
        retired = gate(status="retired", human_review={"reviewer": "owner", "decision": "retire", "reviewed_at": "2026-08-04T00:00:00Z"})
        result = validate_gate_economics([retired], required_gate_ids=("execution-audit",))
        self.assertIn("required gate execution-audit must remain active", result["errors"])

    def test_automatic_retirement_and_telemetry_are_rejected(self):
        result = validate_gate_economics([gate(status="retired", telemetry={"send": True})])
        self.assertTrue(any("prohibited" in error for error in result["errors"]))
        self.assertTrue(any("human_review" in error for error in result["errors"]))

    def test_malformed_and_undeclared_records_fail_closed(self):
        result = validate_gate_economics([{"gate_id": "x"}], required_gate_ids=("required",))
        self.assertGreater(len(result["errors"]), 5)
        self.assertIn("required gate required is missing", result["errors"])

    def test_finding_remediation_and_escaped_outcome_are_recorded(self):
        entry = gate(
            finding={"status": "finding", "finding_ids": ["F-001"]},
            remediation="Add the missing read-back assertion",
            downstream_outcome={"status": "escaped", "event_id": "E-001"},
        )
        self.assertEqual(validate_gate_economics([entry]), {"errors": [], "diagnostics": []})

    def test_unknown_cost_is_explicitly_supported(self):
        self.assertEqual(
            validate_gate_economics([gate(cost_proxy="UNKNOWN")]),
            {"errors": [], "diagnostics": []},
        )

    def test_validation_performs_no_network_write(self):
        with patch("socket.create_connection", side_effect=AssertionError("network access")):
            self.assertEqual(
                validate_gate_economics([gate()]),
                {"errors": [], "diagnostics": []},
            )


if __name__ == "__main__":
    unittest.main()
