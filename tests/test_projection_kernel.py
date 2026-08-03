from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import projection_bundle as bundle
import projection_kernel as kernel
from plan_phase_validation import validate_projection_transaction_evidence


AUTHORITY_BYTES = b"frozen authoritative plan bytes\n"
VERSIONS = {
    "kernel": "projection-kernel/v1",
    "reader": "github-issue-reader/v1",
    "canonicalizer": "github-issue-body/v1",
}
POLICIES = {
    "assurance": "assurance-policy/v1",
    "visual": "visual-applicability/v1",
}
ASSURANCE = {
    "requested": "heavy",
    "effective": "heavy",
    "selection_origin": "legacy_phase_command",
    "legacy_subprofile": None,
}
CAPSULE = {"capsule_id": "capsule-24", "generation": 1, "digest": "a" * 64}
AUTHORITY = {
    "kind": "github_issue",
    "locator": "https://github.com/mknoth197/evidence-gated-delivery/issues/24",
    "source_revision": "cac311f36c83587a62327cb51a864f14b5fec15c80242acdfb7b9514deb74435",
}
INTENT = {
    "risk_classification": "ordinary_scoped_recoverable",
    "authority_ref": "plan-24",
    "staged_action_digest": "b" * 64,
}


def present(payload: object, version: str = "task-projection/v1") -> dict:
    return {
        "state": "present",
        "projection_version": version,
        "payload": payload,
        "payload_digest": bundle.projection_sha256(payload),
    }


def adapter(payload: object, *, seen_ids: list[int] | None = None):
    def run(authority_bytes: bytes, authority_digest: str, versions: dict) -> dict:
        if seen_ids is not None:
            seen_ids.append(id(authority_bytes))
        return {
            "authority_digest": authority_digest,
            "versions": dict(versions),
            "slot": present(payload),
        }

    return run


def run_kernel(**overrides):
    values = {
        "authority_bytes": AUTHORITY_BYTES,
        "authority": AUTHORITY,
        "versions": VERSIONS,
        "policy_versions": POLICIES,
        "assurance": ASSURANCE,
        "capsule_generation": CAPSULE,
        "adapters": {"tasks": adapter({"ids": ["T-001", "T-002"]})},
        "required_slots": ["tasks"],
        "prepared_at": "2026-08-03T19:00:00Z",
        "completed_at": "2026-08-03T19:01:00Z",
        "intent": INTENT,
    }
    values.update(overrides)
    return kernel.run_projection_kernel(**values)


class ProjectionKernelTests(unittest.TestCase):
    def test_every_adapter_receives_one_exact_immutable_buffer(self):
        seen: list[int] = []
        result = run_kernel(
            adapters={
                "tasks": adapter({"ids": ["T-001"]}, seen_ids=seen),
                "visual": adapter({"mode": "none"}, seen_ids=seen),
            },
            required_slots=["tasks", "visual"],
        )
        self.assertEqual(len(set(seen)), 1)
        self.assertEqual(result["receipt"]["final_state"], "sealed")
        self.assertEqual(
            result["bundle"]["authority"]["bytes_digest"],
            bundle.authority_bytes_sha256(AUTHORITY_BYTES),
        )

    def test_prepared_identity_is_deterministic_and_version_sensitive(self):
        first = run_kernel()["bundle"]
        second = run_kernel()["bundle"]
        self.assertEqual(first, second)
        changed = run_kernel(versions={**VERSIONS, "reader": "github-issue-reader/v2"})[
            "bundle"
        ]
        self.assertNotEqual(first["prepared_digest"], changed["prepared_digest"])
        self.assertNotEqual(first["bundle_id"], changed["bundle_id"])

    def test_later_evidence_changes_receipt_not_prepared_identity(self):
        prepared = run_kernel()["bundle"]
        first = bundle.build_projection_transaction_receipt(
            prepared,
            intent=INTENT,
            audit_receipts=[{"audit": "a"}],
            completed_at="2026-08-03T19:01:00Z",
            required_slots=["tasks"],
        )
        second = bundle.build_projection_transaction_receipt(
            prepared,
            intent=INTENT,
            audit_receipts=[{"audit": "b"}],
            completed_at="2026-08-03T19:02:00Z",
            required_slots=["tasks"],
        )
        self.assertEqual(first["prepared_digest"], prepared["prepared_digest"])
        self.assertEqual(second["prepared_digest"], prepared["prepared_digest"])
        self.assertNotEqual(first["receipt_digest"], second["receipt_digest"])

    def test_explicit_policy_omission_can_seal(self):
        omitted = {
            "state": "omitted",
            "policy_rule_id": "LIGHT_NO_TOURNAMENT",
            "reason": "No design choice is requested",
            "evidence_refs": ["scope-24"],
        }
        result = run_kernel(
            adapters={}, explicit_slots={"tournament": omitted}, required_slots=["tournament"]
        )
        self.assertEqual(result["bundle"]["slots"]["tournament"], omitted)
        self.assertEqual(result["receipt"]["final_state"], "sealed")

    def test_pending_and_blocked_slots_never_seal(self):
        for slot in (
            {"state": "pending", "responsible_component": "graph", "next_action": "read back"},
            {
                "state": "blocked",
                "blocker_ref": "BLOCKED_MISSING_AUTHORITY:graph",
                "responsible_component": "graph",
                "next_safe_action": "obtain authority",
            },
        ):
            with self.subTest(state=slot["state"]):
                result = run_kernel(
                    adapters={}, explicit_slots={"graph": slot}, required_slots=["graph"]
                )
                self.assertEqual(result["receipt"]["final_state"], "blocked")
                self.assertTrue(result["receipt"]["blockers"])

    def test_one_conflicting_adapter_blocks_the_whole_transaction(self):
        def conflict(_bytes: bytes, _digest: str, versions: dict) -> dict:
            return {
                "authority_digest": "0" * 64,
                "versions": {**versions, "reader": "other-reader/v1"},
                "slot": present({"bad": True}),
            }

        result = run_kernel(
            adapters={"tasks": adapter({"ok": True}), "visual": conflict},
            required_slots=["tasks", "visual"],
        )
        self.assertEqual(result["bundle"]["slots"]["tasks"]["state"], "present")
        self.assertEqual(result["bundle"]["slots"]["visual"]["state"], "blocked")
        self.assertEqual(result["receipt"]["final_state"], "blocked")
        evidence = json.dumps(result["receipt"]["blockers"])
        self.assertIn("authority.bytes_digest", evidence)
        self.assertIn("versions.reader", evidence)
        self.assertNotIn("partial", result["receipt"]["final_state"])

    def test_unknown_slot_vocabulary_is_a_durable_blocker(self):
        result = run_kernel(
            adapters={
                "tasks": adapter({"ok": True}),
                "invented": adapter({"unsupported": True}),
            },
            required_slots=["tasks"],
        )
        self.assertEqual(result["receipt"]["final_state"], "blocked")
        self.assertIn("BLOCKED_UNSUPPORTED_SCHEMA", json.dumps(result["receipt"]))
        self.assertNotIn("invented", result["bundle"]["slots"])

    def test_adapter_controlled_diagnostics_are_bounded_and_redacted(self):
        secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"

        def raises(_bytes: bytes, _digest: str, _versions: dict) -> dict:
            raise RuntimeError(f"upstream leaked {secret}")

        exception_result = run_kernel(adapters={"tasks": raises})
        exception_evidence = json.dumps(exception_result["receipt"])
        self.assertEqual(exception_result["receipt"]["final_state"], "blocked")
        self.assertIn("RuntimeError", exception_evidence)
        self.assertNotIn(secret, exception_evidence)
        self.assertNotIn("upstream leaked", exception_evidence)

        def unsafe_version(_bytes: bytes, digest: str, versions: dict) -> dict:
            return {
                "authority_digest": digest,
                "versions": {**versions, "reader": secret},
                "slot": present({"bad": True}),
            }

        version_result = run_kernel(adapters={"tasks": unsafe_version})
        version_evidence = json.dumps(version_result["receipt"])
        self.assertEqual(version_result["receipt"]["final_state"], "blocked")
        self.assertIn("versions.reader", version_evidence)
        self.assertNotIn(secret, version_evidence)

    def test_started_external_action_without_readback_blocks(self):
        action = {
            "target": "issue-25",
            "started_evidence": "event-1",
            "mutation_receipt": "exit-0",
            "readback_evidence": None,
            "durable_output": None,
            "state": "started",
        }
        result = run_kernel(external_actions=[action])
        self.assertEqual(result["receipt"]["final_state"], "blocked")
        self.assertIn("BLOCKED_REMOTE_READBACK_MISMATCH", json.dumps(result["receipt"]))

    def test_atomic_envelope_write_preserves_previous_bytes_on_replace_failure(self):
        result = run_kernel()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "projection.json"
            kernel.atomic_write_projection_envelope(
                path, result["bundle"], result["receipt"], required_slots=["tasks"]
            )
            original = path.read_bytes()
            changed = run_kernel(completed_at="2026-08-03T19:03:00Z")
            with patch("projection_kernel.os.replace", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    kernel.atomic_write_projection_envelope(
                        path,
                        changed["bundle"],
                        changed["receipt"],
                        required_slots=["tasks"],
                    )
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob(".projection.json.*.tmp")), [])

    def test_atomic_envelope_write_requires_exact_readback(self):
        result = run_kernel()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "projection.json"
            with patch.object(Path, "read_bytes", return_value=b"tampered"):
                with self.assertRaisesRegex(kernel.ProjectionKernelError, "read-back mismatch"):
                    kernel.atomic_write_projection_envelope(
                        path,
                        result["bundle"],
                        result["receipt"],
                        required_slots=["tasks"],
                    )

    def test_optional_plan_hook_validates_present_evidence_without_cutover(self):
        result = run_kernel()
        data = {
            "projection_transaction_evidence": {
                "bundle": result["bundle"],
                "receipt": result["receipt"],
                "required_slots": ["tasks"],
            }
        }
        self.assertEqual(validate_projection_transaction_evidence({}), [])
        self.assertEqual(validate_projection_transaction_evidence(data), [])
        tampered = copy.deepcopy(data)
        tampered["projection_transaction_evidence"]["receipt"]["prepared_digest"] = "0" * 64
        errors = validate_projection_transaction_evidence(tampered)
        self.assertTrue(any("prepared_digest conflict" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
