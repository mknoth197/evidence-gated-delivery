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
class ReviewValidatorTests(unittest.TestCase):
    @staticmethod
    def implement_evidence() -> dict:
        return {
            "orientation_complete": True,
            "first_mutation_at": "2026-08-04T12:00:00Z",
            "no_mutation_before_approval": True,
            "implementation_workers": [
                {
                    "agent_id": "11111111-1111-4111-8111-111111111111",
                    "status": "completed",
                    "result": "implemented",
                    "ownership": ["scripts/module.py"],
                    "handoff": "done",
                }
            ],
            "test_reviewer": {
                "agent_id": "22222222-2222-4222-8222-222222222222",
                "status": "completed",
                "result": "PASS",
            },
            "acceptance_reviewer": {},
            "visual_artifact_disposition": {"evidence_mode": "none"},
            "quality_gates": [
                {"name": "implement tests", "status": "passed", "evidence": "old"}
            ],
            "pull_request_url": "https://github.com/o/r/pull/1",
        }

    @staticmethod
    def fake_reviewer(_data, entry, label, errors, **_kwargs):
        if not isinstance(entry, dict) or not entry.get("agent_id"):
            errors.append(f"{label} missing")
            return None
        return entry["agent_id"]

    def review_gate_errors(
        self, review: dict, implement: dict | None = None
    ) -> list[str]:
        errors: list[str] = []
        with (
            patch.object(
                validator.successor_visual_validation, "validate", return_value=None
            ),
            patch.object(
                validator.review_phase_validation,
                "validate_reviewer",
                side_effect=self.fake_reviewer,
            ),
            patch.object(validator, "github_readback", return_value=({}, None)),
        ):
            validator.add_implement_errors(
                implement or self.implement_evidence(),
                errors,
                False,
                visual_phase="review",
                review_paths=["scripts/module.py"],
                predecessor_plan={},
                successor_visual_data=review,
            )
        return errors

    def test_review_rejects_stale_implement_gates_and_missing_escalation_reviewer(self):
        errors = self.review_gate_errors(
            {
                "visual_artifact_disposition": {"evidence_mode": "runtime_capture"},
                "acceptance_reviewer": {},
                "unexplained_mockup_gaps": None,
                "quality_gates": [],
                "pull_request_url": "https://github.com/o/r/pull/1",
            }
        )

        self.assertIn("acceptance_reviewer missing", errors)
        self.assertIn("quality_gates must contain structured gate evidence", errors)
        self.assertIn("unexplained_mockup_gaps must equal 0", errors)

    def test_review_accepts_fresh_escalation_reviewer_and_current_gates(self):
        errors = self.review_gate_errors(
            {
                "visual_artifact_disposition": {"evidence_mode": "runtime_capture"},
                "acceptance_reviewer": {
                    "agent_id": "33333333-3333-4333-8333-333333333333",
                    "status": "completed",
                    "result": "PASS",
                },
                "unexplained_mockup_gaps": 0,
                "quality_gates": [
                    {
                        "name": "review tests",
                        "status": "passed",
                        "evidence": "rerun on current PR head",
                    }
                ],
                "pull_request_url": "https://github.com/o/r/pull/1",
            }
        )

        self.assertEqual(errors, [])

    def test_review_rejects_reused_implement_acceptance_agent(self):
        implement = self.implement_evidence()
        implement["acceptance_reviewer"] = {
            "agent_id": "33333333-3333-4333-8333-333333333333",
            "status": "completed",
            "result": "old callback",
        }
        errors = self.review_gate_errors(
            {
                "visual_artifact_disposition": {"evidence_mode": "runtime_capture"},
                "acceptance_reviewer": {
                    "agent_id": "33333333-3333-4333-8333-333333333333",
                    "status": "completed",
                    "result": "changed callback",
                },
                "unexplained_mockup_gaps": 0,
                "quality_gates": [
                    {
                        "name": "review tests",
                        "status": "passed",
                        "evidence": "fresh Review rerun",
                    }
                ],
                "pull_request_url": "https://github.com/o/r/pull/1",
            },
            implement,
        )

        self.assertIn(
            "Review acceptance_reviewer must be fresh from the Implement acceptance reviewer",
            errors,
        )

    def test_malformed_timeline_returns_invalid_instead_of_raising(self):
        errors = validator.validate(
            {
                "mode": "review",
                "run_started_at": "2026-07-29T12:00:00Z",
                "phase_timeline": [],
            },
            "review",
            skip_remote=True,
        )
        self.assertTrue(
            any("phase_timeline must be an object" in error for error in errors)
        )

    def test_mixed_timezone_awareness_returns_invalid_instead_of_raising(self):
        errors = validator.validate(
            {
                "mode": "review",
                "run_started_at": "2026-07-29T12:00:00Z",
                "phase_timeline": {
                    "research_started_at": "2026-07-29T12:01:00",
                    "research_completed_at": "2026-07-29T12:02:00Z",
                    "plan_started_at": "2026-07-29T12:03:00Z",
                    "plan_completed_at": "2026-07-29T12:04:00Z",
                    "implement_completed_at": "2026-07-29T12:05:00Z",
                    "review_completed_at": "2026-07-29T12:06:00Z",
                },
            },
            "review",
            skip_remote=True,
        )
        self.assertTrue(
            any(
                "phase_timeline.research_started_at must be an ISO-8601 timestamp"
                in error
                for error in errors
            )
        )

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
            ["git", "config", "commit.gpgsign", "false"],
            cwd=root,
            check=True,
        )
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

    def test_make_repo_disables_inherited_commit_signing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_repo(root)
            signing = subprocess.run(
                ["git", "config", "--local", "--get", "--bool", "commit.gpgsign"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
        self.assertEqual(signing.stdout.strip(), "false")

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

    def test_imported_review_roles_are_excluded_from_transition_judge(self):
        current = {"phase": "implement", "agent_id": "current"}
        prior = {
            "contestants": [{"agent_id": "plan-contestant"}],
            "implementation_workers": [{"agent_id": "implement-worker"}],
            "test_reviewer": {"agent_id": "implement-reviewer"},
            "trace_audits": [{"agent_id": "implement-auditor"}],
        }
        excluded = validator.transition_judge_excluded_ids(
            {"phase_transition_judgments": [current]},
            current,
            prior_role_data=prior,
        )
        self.assertTrue(
            {"plan-contestant", "implement-worker", "implement-reviewer", "implement-auditor"}
            <= excluded
        )


if __name__ == "__main__":
    unittest.main()
