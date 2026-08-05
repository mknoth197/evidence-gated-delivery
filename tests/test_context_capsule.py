from __future__ import annotations

import json
import multiprocessing
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from context_capsule import (
    BLOCKED_CAPSULE_CONFLICT,
    BLOCKED_CAPSULE_INVALID,
    BLOCKED_SOURCE_DRIFT,
    CapsuleConflict,
    CapsuleInvalid,
    SourceDrift,
    archive,
    checkpoint,
    compact,
    create,
    fork,
    list_blockers,
    resume,
    status,
    supersede,
    verify_chain,
)
import context_capsule


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def create_capsule(path: Path, *, achieved: str = "pending") -> dict:
    return create(
        path,
        capsule_id="capsule-test",
        objective="Preserve compact semantic state",
        settled_decisions=[
            {
                "id": "D-001",
                "decision": "Use immutable generations",
                "evidence_refs": ["E-001"],
                "settled_at": "2026-08-04T12:00:00Z",
            }
        ],
        source_revisions=[
            {"source_id": "plan-24", "revision": "revision-a", "digest": DIGEST_A}
        ],
        evidence_refs=[
            {
                "id": "E-001",
                "kind": "github_issue",
                "locator": "https://github.com/mknoth197/evidence-gated-delivery/issues/24",
                "digest": DIGEST_A,
                "access": "public",
            },
            {
                "id": "E-unused",
                "kind": "test_fixture",
                "locator": "fixture:unused",
                "digest": DIGEST_B,
                "access": "local",
            },
        ],
        execution_frontier={
            "state": "ready",
            "next_action": "Run focused tests",
            "responsible_component": "persistence",
            "blocker_ref": None,
        },
        unresolved_questions=[
            {
                "id": "Q-001",
                "question": "Does independent resume preserve identity?",
                "owner": "persistence",
                "next_evidence_action": "Run the CLI in a new process",
            }
        ],
        next_action={
            "description": "Run focused tests",
            "risk_classification": "ordinary_scoped_recoverable",
            "authority_ref": "plan-24",
        },
        assurance={
            "requested": "heavy",
            "effective": "heavy",
            "achieved": achieved,
            "selection_origin": "legacy_phase_command",
            "legacy_subprofile": None,
        },
        timestamp="2026-08-04T12:00:00Z",
    )


def concurrent_checkpoint(path: str, generation: int, digest: str, start, queue, label: str) -> None:
    start.wait()
    try:
        result = checkpoint(
            path,
            expected_generation=generation,
            expected_digest=digest,
            changes={"objective": f"writer-{label}"},
        )
    except CapsuleConflict as exc:
        queue.put(("conflict", exc.blocker.get("code")))
    else:
        queue.put(("written", result["generation"]))


class ContextCapsuleTests(unittest.TestCase):
    def test_lifecycle_is_digest_chained_and_archive_requires_fork(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "capsule.json"
            first = create_capsule(path)
            second = checkpoint(
                path,
                expected_generation=first["generation"],
                expected_digest=first["digest"],
                changes={"objective": "Checkpoint semantic state"},
                timestamp="2026-08-04T12:01:00Z",
            )
            self.assertEqual(second["generation"], 2)
            self.assertEqual(second["previous_digest"], first["digest"])

            forked_path = root / "fork.json"
            forked = fork(path, forked_path, capsule_id="capsule-fork")
            self.assertEqual(forked["generation"], 1)
            self.assertIsNone(forked["previous_digest"])
            self.assertEqual(forked["parent_capsule"]["digest"], second["digest"])

            compacted = compact(
                path,
                expected_generation=second["generation"],
                expected_digest=second["digest"],
                evidence_refs=[second["evidence_refs"][0]],
            )
            self.assertEqual([item["id"] for item in compacted["evidence_refs"]], ["E-001"])

            superseded = supersede(
                path,
                expected_generation=compacted["generation"],
                expected_digest=compacted["digest"],
                successor={
                    "capsule_id": forked["capsule_id"],
                    "generation": forked["generation"],
                    "digest": forked["digest"],
                },
            )
            self.assertEqual(superseded["execution_frontier"]["state"], "superseded")

            archived = archive(
                path,
                expected_generation=superseded["generation"],
                expected_digest=superseded["digest"],
            )
            self.assertEqual(archived["execution_frontier"]["state"], "archived")
            with self.assertRaises(CapsuleConflict):
                resume(
                    path,
                    current_source_revisions={
                        "plan-24": {"revision": "revision-a", "digest": DIGEST_A}
                    },
                )
            archived_fork = fork(path, root / "archived-fork.json", capsule_id="archived-fork")
            self.assertEqual(archived_fork["parent_capsule"]["digest"], archived["digest"])

    def test_resume_succeeds_in_an_independent_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "capsule.json"
            capsule = create_capsule(path, achieved="light")
            sources = root / "sources.json"
            sources.write_text(
                json.dumps(
                    {"plan-24": {"revision": "revision-a", "digest": DIGEST_A}}
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "context_capsule.py"),
                    "resume",
                    str(path),
                    "--source-revisions",
                    str(sources),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["status"], "RESUMABLE")
            self.assertEqual(output["ref"]["digest"], capsule["digest"])
            self.assertEqual(output["assurance"]["achieved"], "light")

    def test_corruption_fails_closed_and_persists_a_blocker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capsule.json"
            create_capsule(path)
            capsule = json.loads(path.read_text(encoding="utf-8"))
            capsule["objective"] = "tampered without digest"
            path.write_text(json.dumps(capsule), encoding="utf-8")
            result = status(path)
            self.assertEqual(result["status"], "INVALID")
            self.assertEqual(result["blocker"]["code"], BLOCKED_CAPSULE_INVALID)
            self.assertTrue(list_blockers(path))

    def test_two_process_writers_have_one_winner_and_one_cas_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capsule.json"
            capsule = create_capsule(path)
            context = multiprocessing.get_context("fork")
            start = context.Event()
            queue = context.Queue()
            workers = [
                context.Process(
                    target=concurrent_checkpoint,
                    args=(str(path), capsule["generation"], capsule["digest"], start, queue, label),
                )
                for label in ("a", "b")
            ]
            for worker in workers:
                worker.start()
            start.set()
            results = [queue.get(timeout=10) for _ in workers]
            for worker in workers:
                worker.join(timeout=10)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual(sorted(result[0] for result in results), ["conflict", "written"])
            self.assertIn(("conflict", BLOCKED_CAPSULE_CONFLICT), results)
            self.assertEqual(verify_chain(path)["generation"], 2)

    def test_interruption_before_head_replace_preserves_prior_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capsule.json"
            first = create_capsule(path)
            real_atomic_write = context_capsule._atomic_write

            def interrupt_head(target, payload, *, replace=True):
                if target == path:
                    raise OSError("injected before head replacement")
                return real_atomic_write(target, payload, replace=replace)

            with patch.object(context_capsule, "_atomic_write", side_effect=interrupt_head):
                with self.assertRaisesRegex(OSError, "injected"):
                    checkpoint(
                        path,
                        expected_generation=first["generation"],
                        expected_digest=first["digest"],
                        changes={"objective": "Interrupted checkpoint"},
                        timestamp="2026-08-04T12:01:00Z",
                    )
            self.assertEqual(verify_chain(path)["digest"], first["digest"])
            recovered = checkpoint(
                path,
                expected_generation=first["generation"],
                expected_digest=first["digest"],
                changes={"objective": "Recovered checkpoint"},
                timestamp="2026-08-04T12:02:00Z",
            )
            self.assertEqual(recovered["generation"], 2)

    def test_source_drift_checkpoints_truthful_blocked_heavy_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capsule.json"
            original = create_capsule(path)
            with self.assertRaises(SourceDrift) as raised:
                resume(
                    path,
                    current_source_revisions={
                        "plan-24": {"revision": "revision-b", "digest": DIGEST_B}
                    },
                )
            self.assertEqual(raised.exception.blocker["code"], BLOCKED_SOURCE_DRIFT)
            blocked = verify_chain(path)
            self.assertEqual(blocked["generation"], original["generation"] + 1)
            self.assertEqual(blocked["assurance"]["requested"], "heavy")
            self.assertEqual(blocked["assurance"]["effective"], "heavy")
            self.assertEqual(blocked["assurance"]["achieved"], "blocked")
            self.assertEqual(blocked["execution_frontier"]["blocker_ref"], BLOCKED_SOURCE_DRIFT)

    def test_achieved_heavy_capsule_resumes_without_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capsule.json"
            created = create_capsule(path, achieved="heavy")
            resumed = resume(
                path,
                current_source_revisions={
                    "plan-24": {"revision": "revision-a", "digest": DIGEST_A}
                },
            )
            self.assertEqual(resumed["digest"], created["digest"])
            self.assertEqual(resumed["assurance"]["effective"], "heavy")
            self.assertEqual(resumed["assurance"]["achieved"], "heavy")

    def test_evidence_reference_bound_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capsule.json"
            references = [
                {
                    "id": f"E-{index:03d}",
                    "kind": "test_fixture",
                    "locator": f"fixture:{index}",
                    "digest": DIGEST_A,
                    "access": "local",
                }
                for index in range(context_capsule.MAX_EVIDENCE_REFS + 1)
            ]
            with self.assertRaisesRegex(CapsuleInvalid, "evidence_refs exceeds its bound"):
                create(
                    path,
                    capsule_id="bounded-evidence",
                    objective="Reject unbounded evidence references",
                    settled_decisions=[],
                    source_revisions=[],
                    evidence_refs=references,
                    execution_frontier={
                        "state": "ready",
                        "next_action": "Stop",
                        "responsible_component": "persistence",
                        "blocker_ref": None,
                    },
                    unresolved_questions=[],
                    next_action={
                        "description": "Stop",
                        "risk_classification": "ordinary_scoped_recoverable",
                        "authority_ref": "local-test",
                    },
                    assurance={
                        "requested": "light",
                        "effective": "light",
                        "achieved": "pending",
                        "selection_origin": "explicit_assurance",
                        "legacy_subprofile": None,
                    },
                    timestamp="2026-08-04T12:00:00Z",
                )

    def test_compaction_preserves_active_questions_decisions_and_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capsule.json"
            capsule = create_capsule(path)
            with self.assertRaisesRegex(CapsuleInvalid, "unresolved questions"):
                compact(
                    path,
                    expected_generation=capsule["generation"],
                    expected_digest=capsule["digest"],
                    unresolved_questions=[],
                )
            with self.assertRaisesRegex(CapsuleInvalid, "settled decisions"):
                compact(
                    path,
                    expected_generation=capsule["generation"],
                    expected_digest=capsule["digest"],
                    settled_decisions=[],
                )
            self.assertEqual(verify_chain(path)["digest"], capsule["digest"])

    def test_privacy_minimization_rejects_secret_fields_and_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capsule.json"
            with self.assertRaisesRegex(CapsuleInvalid, "privacy minimization"):
                create(
                    path,
                    capsule_id="private",
                    objective="Never persist sensitive payloads",
                    settled_decisions=[],
                    source_revisions=[],
                    evidence_refs=[],
                    execution_frontier={
                        "state": "ready",
                        "next_action": "Stop",
                        "responsible_component": "persistence",
                        "blocker_ref": None,
                    },
                    unresolved_questions=[],
                    next_action={
                        "description": "Stop",
                        "risk_classification": "ordinary_scoped_recoverable",
                        "authority_ref": "local-test",
                    },
                    assurance={
                        "requested": "light",
                        "effective": "light",
                        "achieved": "pending",
                        "selection_origin": "explicit_assurance",
                        "legacy_subprofile": None,
                    },
                    privacy={
                        "classification": "restricted",
                        "redactions": [],
                        "omitted_fields": ["transcript", "hidden_reasoning", "credentials"],
                        "retention_hint": "Bearer abcdefghijklmnop",
                    },
                )
            self.assertEqual(list_blockers(path)[0]["code"], BLOCKED_CAPSULE_INVALID)

    def test_invalid_heavy_assurance_does_not_erase_resumable_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capsule.json"
            blocked = create_capsule(path, achieved="blocked")
            resumed = resume(
                path,
                current_source_revisions={
                    "plan-24": {"revision": "revision-a", "digest": DIGEST_A}
                },
            )
            self.assertEqual(resumed["digest"], blocked["digest"])
            self.assertEqual(resumed["objective"], "Preserve compact semantic state")
            self.assertEqual(resumed["assurance"]["achieved"], "blocked")


if __name__ == "__main__":
    unittest.main()
