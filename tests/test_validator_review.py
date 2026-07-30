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


if __name__ == "__main__":
    unittest.main()
