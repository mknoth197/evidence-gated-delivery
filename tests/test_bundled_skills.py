from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDITOR = ROOT / "bundled-skills" / "plan-auditor" / "SKILL.md"
GRAPH = ROOT / "bundled-skills" / "plan-to-graph" / "SKILL.md"
NOTICE = ROOT / "THIRD_PARTY_NOTICES.md"
PROVENANCE = ROOT / "references" / "source-provenance.json"


def frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return {}
    result: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator:
            result[key.strip()] = value.strip()
    return result


class BundledSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.auditor = AUDITOR.read_text()
        self.graph = GRAPH.read_text()
        self.package = json.loads((ROOT / "skills.sh.json").read_text())
        self.provenance = json.loads(PROVENANCE.read_text())

    def test_package_declares_existing_portable_skills(self):
        declared = {item["name"]: item["path"] for item in self.package["skills"]}
        self.assertEqual(declared["plan-auditor"], "bundled-skills/plan-auditor/SKILL.md")
        self.assertEqual(declared["plan-to-graph"], "bundled-skills/plan-to-graph/SKILL.md")
        for name in ("plan-auditor", "plan-to-graph"):
            path = ROOT / declared[name]
            self.assertTrue(path.is_file())
            self.assertEqual(frontmatter(path.read_text()).get("name"), name)

    def test_plan_auditor_contract_is_fresh_and_actionable(self):
        required = (
            "Independent Plan spec auditor",
            "PA-001",
            "Blocker",
            "High",
            "Medium",
            "bounded_question",
            "targeted_patch",
            "verification_implication",
            "downstream_instruction",
            "remediation_recheck",
            "final_remote",
            "predecessor",
            "canonical remote body",
            "Exclude credentials",
            "private prompts",
            "PII",
        )
        for token in required:
            self.assertIn(token, self.auditor)
        self.assertIn("GitHub implementation issue", self.auditor)
        self.assertIn("another fresh session", self.auditor)
        self.assertNotIn(".github/specs", self.auditor)

    def test_plan_to_graph_contract_is_protected_and_recoverable(self):
        required = (
            "immutable account ID",
            "native child issues",
            "native blocked-by",
            "complete task Markdown",
            "canonical child-body SHA-256",
            "exact frozen draft",
            "validated Plan is the publication authority",
            "EXACT_MATCH",
            "AUTHORIZED_MISSING",
            "CONFLICT",
            "exact subset",
            "action record",
            "immediately before dispatching",
            "attempted",
            "verified",
            "blocked",
            "remote read-back",
            "stable marker",
            "Exclude credentials",
            "private prompts",
            "PII",
        )
        for token in required:
            self.assertIn(token, self.graph)
        mutation_gate = self.graph.index("## Automatic publication gate")
        transaction = self.graph.index("## Transaction and action ledger")
        self.assertLess(mutation_gate, transaction)
        self.assertNotIn(".github/specs", self.graph)

    def test_provenance_and_notice_are_pinned_and_complete(self):
        source = self.provenance["sources"][0]
        expected_commit = "b82466fe5163ae2e2a469d13b2e19714c7466464"
        self.assertEqual(self.provenance["schema_version"], "source-provenance/v1")
        self.assertEqual(source["commit"], expected_commit)
        self.assertEqual(source["license"], "BSD-2-Clause")
        self.assertEqual(source["decision"], "clean-room portable reimplementation")
        self.assertEqual(source["notice"], "THIRD_PARTY_NOTICES.md")
        notice = NOTICE.read_text()
        self.assertIn(expected_commit, notice)
        self.assertIn("Copyright (c) 2026, lousy-agents", notice)
        self.assertIn("Redistribution and use in source and binary forms", notice)
        self.assertIn('THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"', notice)
        self.assertEqual(
            source["installed_notices"],
            [
                "bundled-skills/plan-auditor/THIRD_PARTY_NOTICE.md",
                "bundled-skills/plan-to-graph/THIRD_PARTY_NOTICE.md",
            ],
        )
        for relative_path in source["installed_notices"]:
            installed_notice = (ROOT / relative_path).read_text()
            self.assertIn(expected_commit, installed_notice)
            self.assertIn("Copyright (c) 2026, lousy-agents", installed_notice)
            self.assertIn(
                'THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"',
                installed_notice,
            )

    def test_bundled_skills_have_no_fixed_repository_or_local_source_authority(self):
        combined = self.auditor + self.graph
        forbidden = (
            "mknoth197/evidence-gated-delivery",
            "/Users/",
            "/tmp/",
            "OWNER/REPO",
            "local spec as authority",
        )
        for token in forbidden[:-1]:
            self.assertNotIn(token, combined)
        self.assertIn("Do not", combined[combined.index(forbidden[-1]) - 20 : combined.index(forbidden[-1]) + 80])

    def test_release_sync_verifier_detects_exact_copy_and_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            installed = Path(directory) / "evidence-gated-delivery"
            shutil.copytree(
                ROOT,
                installed,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
            command = [
                sys.executable,
                str(ROOT / "scripts" / "verify_skill_sync.py"),
                "--installed-skill",
                str(installed),
            ]
            exact = subprocess.run(
                command, text=True, capture_output=True, check=False
            )
            self.assertEqual(exact.returncode, 0, exact.stdout + exact.stderr)
            (installed / "SKILL.md").write_text("drift\n")
            drift = subprocess.run(
                command, text=True, capture_output=True, check=False
            )
            self.assertEqual(drift.returncode, 1)
            self.assertIn("'status': 'DRIFT'", drift.stdout)

    def test_validation_modules_remain_focused(self):
        scripts = ROOT / "scripts"
        facades = ("plan_protocol.py", "visual_applicability.py")
        focused = (
            "collaboration_receipts.py",
            "github_graph_adapter.py",
            "plan_audits.py",
            "plan_events.py",
            "plan_graph.py",
            "plan_phase_validation.py",
            "plan_projection_validation.py",
            "plan_protocol_core.py",
            "plan_tasks.py",
            "review_phase_validation.py",
            "trace_validation.py",
            "visual_core.py",
            "visual_inventory.py",
            "visual_policy.py",
            "workflow_gate_validation.py",
        )
        for name in facades:
            self.assertLess(
                len((scripts / name).read_text().splitlines()),
                100,
                f"{name} must remain a thin compatibility facade",
            )
        for name in focused:
            self.assertLess(
                len((scripts / name).read_text().splitlines()),
                700,
                f"{name} has accumulated unrelated validation responsibilities",
            )
        self.assertLess(
            len((scripts / "validate_run.py").read_text().splitlines()),
            1000,
            "validate_run.py must remain orchestration, not a god validator",
        )


if __name__ == "__main__":
    unittest.main()
