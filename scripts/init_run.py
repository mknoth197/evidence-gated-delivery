#!/usr/bin/env python3
"""Create an evidence-gated-delivery run manifest with a captured repository baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from plan_protocol import (
    ACTIVATION_RECEIPT_V2,
    PLAN_PROTOCOL_V2,
    WORKFLOW_VERSION_V2,
    PlanProtocolError,
    append_plan_event,
    activation_receipt_binds_workflow_identity,
    protocol_activation_receipt_path,
    record_protocol_activation,
)
from intent_router import HARD_STOP_ACTIONS, resolve_assurance_invocation, route
from context_capsule import (
    create as create_context_capsule,
    status as context_capsule_status,
    verify_chain as verify_context_capsule,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def spec_hashes(root: Path) -> dict[str, str]:
    spec_root = root / ".github" / "specs"
    if not spec_root.exists():
        return {}
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(spec_root.rglob("*"))
        if path.is_file()
    }


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:48] or "run"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        required=True,
        choices=("quick", "balanced", "research", "plan", "orchestrate", "implement", "review", "status"),
    )
    parser.add_argument("--goal", required=True)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--tier", choices=("quick", "balanced", "deep"))
    parser.add_argument("--assurance", choices=("light", "heavy"))
    parser.add_argument("--routing-decision", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    root = args.repo.expanduser().resolve()
    if not root.is_dir() or not (root / ".git").exists():
        print(f"error: {root} is not a git worktree", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc).replace(microsecond=0)
    now_text = now.isoformat().replace("+00:00", "Z")
    run_id = args.run_id or f"{slug(args.goal)}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    output = args.output or Path(f"/tmp/evidence-gated-delivery-{run_id}.json")
    if output.exists():
        print(f"error: refusing to overwrite existing manifest {output}", file=sys.stderr)
        return 2
    parent_thread_id = os.environ.get("CODEX_THREAD_ID", "")
    starting_commit = git(root, "rev-parse", "HEAD")
    activation_recorded_at = now_text
    activation_event_id = None
    activation_path = protocol_activation_receipt_path(run_id)
    if activation_path.exists():
        try:
            stranded = json.loads(activation_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PlanProtocolError("stranded initialization receipt is unreadable") from exc
        binds_workflow_identity = activation_receipt_binds_workflow_identity(
            stranded
        )
        if not binds_workflow_identity:
            raise PlanProtocolError(
                "legacy stranded activation receipt is quarantined; "
                "authenticated workflow-identity upgrade required"
            )
        stable_bindings = {
            "run_id": run_id,
            "parent_thread_id": parent_thread_id,
            "repo_root": str(root),
            "starting_commit": starting_commit,
            "mode": args.mode,
            "goal": args.goal,
            "workflow_version": WORKFLOW_VERSION_V2,
            "plan_protocol_version": PLAN_PROTOCOL_V2,
        }
        if not isinstance(stranded, dict) or any(
            stranded.get(field) != value for field, value in stable_bindings.items()
        ):
            raise PlanProtocolError(
                "stranded initialization receipt does not match the requested run"
            )
        now_text = str(stranded.get("run_started_at", ""))
        activation_recorded_at = str(stranded.get("activated_at", ""))
        activation_event_id = stranded.get("activation_event_id")
    spec_status = [
        line
        for line in git(root, "status", "--porcelain", "--", ".github/specs").splitlines()
        if line.strip()
    ]

    routing_decision = None
    if args.routing_decision:
        routing_decision = json.loads(args.routing_decision.read_text(encoding="utf-8"))
    if args.tier and routing_decision is None:
        raise ValueError("--tier requires a --routing-decision produced by intent_router.py")
    if args.tier and routing_decision.get("tier") != args.tier:
        raise ValueError("--tier must match the routing decision tier")
    if routing_decision is None and args.assurance:
        routing_decision = route(
            {}, requested_tier="deep" if args.assurance == "heavy" else None
        )
    delivery_tier = args.tier or (routing_decision or {}).get("tier", "deep")
    if delivery_tier not in {"quick", "balanced", "deep"}:
        raise ValueError("routing decision must select quick, balanced, or deep")
    requested_tier_by_mode = {"quick": "quick", "balanced": "balanced"}
    if args.mode in requested_tier_by_mode and delivery_tier != requested_tier_by_mode[args.mode]:
        raise ValueError("Quick and Balanced modes must match the routing decision tier")
    uses_legacy_tier = bool(args.tier or args.routing_decision or args.mode in {"quick", "balanced"})
    if args.assurance and uses_legacy_tier:
        raise ValueError("BLOCKED_ASSURANCE_SELECTION: explicit assurance cannot be combined with a legacy tier")
    assurance_tokens = (
        ["--assurance", args.assurance, args.mode]
        if args.assurance
        else [args.mode]
    )
    assurance_selection = resolve_assurance_invocation(
        assurance_tokens,
        legacy_tier=delivery_tier if uses_legacy_tier else None,
    )
    capsule_path = output.parent / f".{output.name}.context-capsule.json"
    source_digest = hashlib.sha256(starting_commit.encode("utf-8")).hexdigest()
    capsule_assurance = {
        "requested": assurance_selection["requested_assurance"]
        or assurance_selection["requested_legacy_tier"]
        or assurance_selection["effective_assurance"],
        "effective": assurance_selection["effective_assurance"],
        "achieved": assurance_selection["achieved_assurance"],
        "selection_origin": assurance_selection["selection_origin"],
        "legacy_subprofile": assurance_selection["legacy_subprofile"],
    }
    if capsule_path.exists():
        capsule_result = context_capsule_status(capsule_path)
        if capsule_result.get("status") != "VALID":
            raise RuntimeError("stranded context capsule is invalid")
        capsule_ref = capsule_result["ref"]
        if capsule_ref.get("capsule_id") != f"capsule-{run_id}":
            raise RuntimeError("stranded context capsule does not match the requested run")
        stranded_capsule = verify_context_capsule(capsule_path)
        if (
            stranded_capsule.get("objective") != args.goal
            or stranded_capsule.get("source_revisions")
            != [
                {
                    "source_id": "repository-baseline",
                    "revision": starting_commit,
                    "digest": source_digest,
                }
            ]
            or stranded_capsule.get("assurance") != capsule_assurance
        ):
            raise RuntimeError("stranded context capsule does not match workflow identity")
    else:
        capsule = create_context_capsule(
            capsule_path,
            capsule_id=f"capsule-{run_id}",
            objective=args.goal,
            settled_decisions=[],
            source_revisions=[
                {
                    "source_id": "repository-baseline",
                    "revision": starting_commit,
                    "digest": source_digest,
                }
            ],
            evidence_refs=[],
            execution_frontier={
                "state": "ready",
                "next_action": f"Begin {args.mode} work",
                "responsible_component": "evidence-gated-delivery",
                "blocker_ref": None,
            },
            unresolved_questions=[],
            next_action={
                "description": f"Begin {args.mode} work",
                "risk_classification": "ordinary_scoped_recoverable",
                "authority_ref": "repository-baseline",
            },
            assurance=capsule_assurance,
            timestamp=now_text,
        )
        capsule_ref = {
            field: capsule[field]
            for field in ("schema_version", "capsule_id", "generation", "digest")
        }
    capsule_ref["locator"] = str(capsule_path)
    manifest = {
        "run_id": run_id,
        "run_started_at": now_text,
        "parent_thread_id": parent_thread_id,
        "phase_timeline": {
            "research_started_at": now_text,
            "research_completed_at": "",
            "plan_started_at": "",
            "plan_completed_at": "",
            "implement_completed_at": "",
            "review_completed_at": "",
        },
        "mode": args.mode,
        **assurance_selection,
        "assurance": assurance_selection,
        "goal": args.goal,
        "selected_mode_reason": "",
        "repo_root": str(root),
        "starting_commit": starting_commit,
        "initial_spec_status": spec_status,
        "initial_spec_hashes": spec_hashes(root),
        "approved_artifact_hosts": [],
        "workflow_version": WORKFLOW_VERSION_V2,
        "delivery_tier": delivery_tier,
        "intent_routing": routing_decision or {
            "tier": "deep",
            "risk_score": None,
            "evidence": {},
            "hard_floor_reasons": ["legacy_default"],
            "authority_envelope": {"ordinary_scoped_work": [], "requires_explicit": []},
            "rationale": "legacy initialization defaults to Deep",
        },
        "tier_evidence": {"sources": [], "checks": [], "external_actions": []},
        "gate_economics": [],
        "execution_frontier": {"next_material_action": "", "state": "ready", "recovery_state": "continue", "hard_boundary": ""},
        "progress_events": [],
        "progress_evidence": [],
        "open_questions": [],
        "gate_inventory": [],
        "automation_policy": {
            "default_mode": "autonomous",
            "auto_transition_min_confidence": 8,
            "stop_before_phases": [],
            "released_stop_gates": [],
            "hard_stop_categories": [
                *sorted(HARD_STOP_ACTIONS),
            ],
        },
        "phase_transition_judgments": [],
        "automation_decisions": [],
        "predecessor_evidence": {},
        "unresolved_hard_stops": [],
        "context_capsule_ref": capsule_ref,
        "projection_transaction_evidence_required": True,
        "projection_transaction_evidence": {},
        "phase_retrospectives": [],
        "retrospective_baseline": {},
        "initiative_identity": {
            "name": "",
            "slug": "",
            "research_issue_url": "",
            "implementation_issue_url": "",
        },
        "trace_audits": [],
        "research_issue_url": "",
        "research_evidence": {
            "live_product": [],
            "source": [],
            "live_data": [],
            "github": [],
        },
        "contestants": [],
        "judges": [],
        "contestant_images": [],
        "judge_rubric": [],
        "semantic_visual_reviews": [],
        "visual_artifact_disposition": {
            "policy_version": "visual-applicability/v1",
            "decision": "",
            "evidence_mode": "",
            "matched_triggers": [],
            "scoped_components": [],
            "evidence": [],
            "uncertainty": [],
            "scope_inventory_status": "",
            "scope_inventory_sha256": "",
            "phase_binding": {
                "phase": "plan",
                "authoritative_issue_body_sha256": "",
                "recompute_at": ["implement-orientation", "review"],
            },
            "evaluated_at": "",
        },
        "runtime_visual_evidence": [],
        "visual_user_directions": [],
        "rejected_visual_artifacts": [],
        "plan_protocol_version": "plan-protocol/v2",
        "plan_protocol_initialized_at": now_text,
        "plan_events": [],
        "plan_audits": [],
        "graph_policy_receipt": {},
        "graph_capability_receipt": {},
        "graph_draft": {},
        "graph_actions": [],
        "graph_remote_state": {},
        "selected_winner": "",
        "synthesis_confidence": 0,
        "synthesized_differentiators": [],
        "rejected_differentiators": [],
        "final_image_iterations": [],
        "final_image_url": "",
        "feature_to_spec_redirected": False,
        "mockup_accounting_rows": 0,
        "acceptance_criteria_count": 0,
        "implementation_task_count": 0,
        "out_of_scope": [],
        "frozen_constraints": [],
        "implementation_issue_url": "",
        "orientation_complete": False,
        "approval_requested": False,
        "approval_granted": False,
        "approval_evidence": {"quote": "", "received_at": ""},
        "first_mutation_at": "",
        "no_mutation_before_approval": True,
        "implementation_workers": [],
        "test_reviewer": {},
        "acceptance_reviewer": {},
        "unexplained_mockup_gaps": None,
        "quality_gates": [],
        "pull_request_url": "",
        "review_dispositions_recorded": False,
        "remote_checks_reported": False,
        "continuing_to": "",
        "next_invocation": "",
    }
    activation_event = append_plan_event(
        manifest["plan_events"],
        "protocol_initialized",
        {
            "plan_protocol_version": manifest["plan_protocol_version"],
            "starting_commit": manifest["starting_commit"],
        },
        recorded_at=activation_recorded_at,
        event_id=activation_event_id,
    )
    record_protocol_activation(manifest, activation_event)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"status": "INITIALIZED", "run_id": run_id, "manifest": str(output)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
