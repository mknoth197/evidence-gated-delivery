from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import init_run
from plan_protocol import PlanProtocolError, protocol_activation_receipt_path


class InitRunRecoveryTests(unittest.TestCase):
    def test_retry_recovers_stranded_activation_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repository,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=repository,
                check=True,
            )
            subprocess.run(
                ["git", "config", "commit.gpgsign", "false"],
                cwd=repository,
                check=True,
            )
            (repository / "README.md").write_text("test\n")
            subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "test: initialize"],
                cwd=repository,
                check=True,
            )
            output = root / "run.json"
            argv = [
                "init_run.py",
                "--mode",
                "research",
                "--goal",
                "Recover init",
                "--repo",
                str(repository),
                "--run-id",
                "recover-init",
                "--output",
                str(output),
            ]
            environment = {
                "CODEX_HOME": str(root / "codex"),
                "CODEX_THREAD_ID": "019f0000-0000-7000-8000-000000000001",
            }
            with patch.dict(os.environ, environment), patch.object(
                sys, "argv", argv
            ), patch.object(Path, "write_text", side_effect=OSError("injected")):
                with self.assertRaises(OSError):
                    init_run.main()
                receipt_path = protocol_activation_receipt_path("recover-init")
                self.assertTrue(receipt_path.is_file())
                receipt = json.loads(receipt_path.read_text())
            with patch.dict(os.environ, environment), patch.object(sys, "argv", argv):
                self.assertEqual(init_run.main(), 0)
            manifest = json.loads(output.read_text())
            self.assertEqual(manifest["run_started_at"], receipt["run_started_at"])
            self.assertEqual(
                manifest["plan_events"][0]["event_id"],
                receipt["activation_event_id"],
            )
            rebound_argv = [
                "init_run.py",
                "--mode",
                "review",
                "--goal",
                "Different workflow",
                "--repo",
                str(repository),
                "--run-id",
                "recover-init",
                "--output",
                str(root / "rebound.json"),
            ]
            with patch.dict(os.environ, environment), patch.object(
                sys, "argv", rebound_argv
            ):
                with self.assertRaises(PlanProtocolError):
                    init_run.main()


if __name__ == "__main__":
    unittest.main()
