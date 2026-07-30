# Run Manifest

Create one JSON manifest per run under `/tmp`:

```text
/tmp/evidence-gated-delivery-<run-id>.json
```

The manifest is an execution receipt, not a planning artifact. GitHub Issues remain authoritative.

## Base Shape

```json
{
  "run_id": "stable-id",
  "run_started_at": "2026-07-23T12:00:00Z",
  "parent_thread_id": "current Codex thread UUID",
  "phase_timeline": {
    "research_started_at": "",
    "research_completed_at": "",
    "plan_started_at": "",
    "plan_completed_at": ""
  },
  "mode": "orchestrate",
  "goal": "User goal",
  "selected_mode_reason": "Natural-language request asked to run the evaluation loop",
  "repo_root": "/absolute/repository/path",
  "starting_commit": "full git sha",
  "initial_spec_status": [],
  "initial_spec_hashes": {},
  "approved_artifact_hosts": [],
  "workflow_version": "evidence-gated-delivery/continuous-improvement-v1",
  "automation_policy": {
    "default_mode": "autonomous",
    "auto_transition_min_confidence": 8,
    "stop_before_phases": [],
    "released_stop_gates": [],
    "hard_stop_categories": ["protected_external_write", "destructive_or_irreversible", "production_or_release", "missing_authority"]
  },
  "phase_transition_judgments": [],
  "automation_decisions": [],
  "unresolved_hard_stops": [],
  "phase_retrospectives": [],
  "retrospective_baseline": {},
  "initiative_identity": {
    "name": "",
    "slug": "",
    "research_issue_url": "",
    "implementation_issue_url": ""
  },
  "visual_grounding": [],
  "external_actions": [],
  "trace_audits": [],
  "research_issue_url": "",
  "research_evidence": {
    "live_product": [],
    "source": [],
    "live_data": [],
    "github": []
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
    "phase_binding": {"phase":"plan","authoritative_issue_body_sha256":"","recompute_at":["implement-orientation","review"]},
    "evaluated_at": ""
  },
  "runtime_visual_evidence": [],
  "rejected_visual_artifacts": [],
  "plan_protocol_version": "plan-protocol/v2",
  "visual_user_directions": [
    "Persist the exact effective user direction used by visual-applicability/v1."
  ],
  "plan_protocol_initialized_at": "",
  "plan_events": [],
  "plan_audits": [],
  "graph_policy_receipt": {},
  "graph_capability_receipt": {},
  "graph_draft": {},
  "graph_authorization": {},
  "graph_actions": [],
  "graph_remote_state": {},
  "selected_winner": "",
  "synthesis_confidence": 0,
  "synthesized_differentiators": [],
  "rejected_differentiators": [],
  "final_image_iterations": [],
  "final_image_url": "",
  "feature_to_spec_redirected": false,
  "mockup_accounting_rows": 0,
  "acceptance_criteria_count": 0,
  "implementation_task_count": 0,
  "out_of_scope": [],
  "frozen_constraints": [],
  "implementation_issue_url": "",
  "orientation_complete": false,
  "approval_requested": false,
  "approval_granted": false,
  "approval_evidence": {
    "quote": "",
    "received_at": ""
  },
  "first_mutation_at": "",
  "no_mutation_before_approval": true,
  "implementation_workers": [],
  "test_reviewer": {},
  "acceptance_reviewer": {},
  "unexplained_mockup_gaps": null,
  "quality_gates": [],
  "pull_request_url": "",
  "review_dispositions_recorded": false,
  "remote_checks_reported": false,
  "continuing_to": "",
  "next_invocation": ""
}
```

## Evidence Entries

Use concrete strings, not booleans:

- Browser URL, screenshot path, or runtime observation.
- Source file and line or test name.
- Query ID, table, API result, or freshness observation.
- GitHub issue, PR, check, or discussion URL.

## Plan Protocol v2

New manifests include `plan_protocol_version: plan-protocol/v2`, a `protocol_initialized` event,
Plan-audit receipts, graph-policy receipt, and graph transaction fields. The full schemas and
legacy/migration rules are defined in [plan-protocol.md](plan-protocol.md). A missing version always
fails closed; a legacy run must explicitly retain `plan-protocol/v1` before resume or migration.
Migration also persists `workflow_version: evidence-gated-delivery/plan-protocol-v2` and a
write-once versioned external activation receipt derived from `run_id`. The receipt binds the activation
event, authenticated parent thread, run start, repository baseline, goal, mode, workflow, and protocol outside
the mutable manifest/event chain. Validation recovers it by exact run ID or authenticated parent
thread alone, then compares every other binding; identity substitution or restored legacy fields
therefore fail even if migration events are removed. A v2 manifest without its authenticated
external receipt, or without the receipt-bound activation event, fails closed.
Receipts created before activation schema versioning remain valid against their authenticated
legacy field set; new receipts additionally bind goal and mode and cannot be rebound.

`plan_events` must remain append-only and hash-chained. A `CHECKPOINT_VALID` event is diagnostic;
only the phase validator may emit `VALID`. `GRAPH_REQUIRED` also requires a current capability
receipt, exact draft authorization, ordered attempted/verified action records, and authenticated
remote graph state. Graph publication is a separately authorized external write.

## Initiative identity, visual grounding, and external actions

For Plan and later phases, initiative_identity binds exactly one name and slug to the research and
implementation issue URLs. Both URL values must exactly match the manifest fields. A newly discovered
product surface or technology experiment starts a separate Research run and cannot reuse this Plan,
tournament, mockup, or approval gate.

visual_artifact_disposition binds `visual-applicability/v1` to the authoritative Plan issue and a
canonical inventory of the scoped deliverable, effective user direction, stable acceptance IDs,
task IDs, affected-module entries, intended or actual paths, and repository evidence. Valid modes
are `none`, `runtime_capture`, and `generative_mockup`. `none` requires complete positive
nonvisual coverage, exact authoritative deliverable binding, and empty image receipts.
`runtime_capture` requires current runtime evidence. Each evidence record names covered scope IDs,
an allowed capture kind, timezone-aware capture timestamp, bounded evidence text, a readable absolute local
artifact path, and a lowercase SHA-256 matching those artifact bytes. Sufficiency defaults false
and is recomputed per scope entry from authoritative evidence; unreadable, digest-mismatched,
content-type-mismatched, or stale evidence cannot satisfy the gate. Embedded sufficiency values
are never validation authority.
but no ImageGen. `generative_mockup` requires the complete visual tournament and durable
publication receipts.

visual_grounding records current product-shell observations used by `runtime_capture` or
`generative_mockup`: surface, live URL, existing screenshot path, its SHA-256, observation time,
and source components. It may be empty in mode `none`. A remembered or generic dashboard shell is
not evidence.

external_actions always records plan-issue-readback and research-issue-readback. It records
final-mockup-publication only for `generative_mockup`.
Each record includes id, kind, state, attempted_at, tool_event_id, result, and readback_evidence.
States are not_started, attempted, blocked, and verified. A completed Plan requires every listed
action to be verified; attempted or blocked must be reported as such with the repair invocation.

## Agent Entries

Contestant:

```json
{
  "candidate_id":"candidate-a",
  "agent_id":"019f0000-0000-7000-8000-000000000001",
  "status":"completed",
  "concept":"...",
  "visual_brief":"...",
  "result":"Completed contestant result"
}
```

Judge:

```json
{
  "agent_id":"019f0000-0000-7000-8000-000000000004",
  "status":"completed",
  "verdict":"...",
  "scorecard":{"candidate-a":85,"candidate-b":78,"candidate-c":74},
  "confidence":8,
  "result":"Completed judge result"
}
```

Implementation worker:

```json
{
  "agent_id":"019f0000-0000-7000-8000-000000000006",
  "status":"completed",
  "ownership":["path/or/module"],
  "handoff":"completed result",
  "result":"Implemented the assigned slice and reported verification evidence"
}
```

All agent IDs must be unique. Judges must not overlap contestants.

## Phase Retrospectives

Use the fixed rubric and recorder in `continuous-improvement.md`. A Plan validation requires a
completed Research retrospective; Implement requires Research and Plan; Review requires Research,
Plan, and Implement. Each item needs evidence for every rubric dimension. A below-threshold or
degraded score must include rechecked remediation before its successor phase can validate.

### Delegated execution-audit receipts

Codex Desktop collaboration sessions persist the spawn and callback in the parent trace and the
UUID-backed completion in the child trace, but may not replay the assignment as a child `user`
message. Use `collaboration_delegated` with both identities:

```json
{
  "phase":"research",
  "receipt_kind":"collaboration_delegated",
  "agent_id":"019f0000-0000-7000-8000-000000000001",
  "agent_path":"/root/research_audit",
  "role_marker":"Execution auditor phase: research",
  "status":"completed",
  "verdict":"PASS",
  "result":"PASS ... E1 ...",
  "result_sha256":"SHA-256 of the exact child completion and parent callback",
  "verified_event_ids":["E1"]
}
```

The validator requires the child metadata, parent spawn call and start event, exact parent callback,
child task completion, agent UUID, agent path, parent ID, timestamps, and result hash to agree.

## Autonomous Transition Judgments

The workflow defaults to autonomous transition. A human can add `plan`, `implement`, or `review`
to `automation_policy.stop_before_phases` at any time. That stops the named successor even if its
judge gives a 10/10. Resuming requires a `released_stop_gates` entry with `phase`, `released_at`,
and the exact `user_evidence`.

Each predecessor phase records a fresh Phase Transition Judge receipt bound to that predecessor's
first `VALID` receipt:

```json
{
  "phase": "plan",
  "successor_phase": "implement",
  "agent_id": "independent-judge-id",
  "status": "pass",
  "recommendation": "proceed",
  "confidence": 8,
  "technical_accuracy_score": 3,
  "evidence_ids": ["E1", "E2"],
  "blocking_findings": [],
  "completed_at": "2026-07-25T12:00:00Z",
  "phase_receipt_sha256": "64 lowercase hex characters",
  "result_sha256": "64 lowercase hex characters"
}
```

An `automation_decisions` entry binds `auto_proceed` to the judge's `result_sha256`. The validator
rejects confidence below 8, a non-integer confidence, technical accuracy below 3/4, high/critical
findings, stale receipt bindings, judge/auditor identity overlap, open human stops, and unresolved
hard-stop categories.

### Realtime execution-audit receipt

When the parent session has `thread_source: realtime_voice`, the delegated auditor is represented
in the parent rollout trace by a depth-one agent path rather than a standalone UUID session. Use:

```json
{
  "phase":"research",
  "receipt_kind":"realtime_delegated",
  "agent_id":"/root/research_audit",
  "role_marker":"Execution auditor phase: research",
  "status":"completed",
  "verdict":"PASS",
  "result":"PASS ... E1 ...",
  "result_sha256":"SHA-256 of the exact callback payload",
  "verified_event_ids":["E1"]
}
```

This is valid only when the parent rollout trace contains the matching delegated start event and
completed callback. It is not a general substitute for UUID-backed agent sessions.

ImageGen receipt:

```json
{
  "candidate_id":"candidate-a",
  "imagegen_call_id":"call-or-exec-id",
  "path":"/absolute/path/to/generated-image.png",
  "sha256":"64 lowercase hex characters"
}
```

In `generative_mockup`, final iterations use the same shape plus numeric `confidence`. Every path
and call ID must be distinct across candidate and final images. `final_image_url` must be a durable
HTTP(S) URL included in the published implementation issue. In `none` and `runtime_capture`,
contestant_images, semantic_visual_reviews, final_image_iterations, and final_image_url stay empty;
rejected exploratory artifacts belong in rejected_visual_artifacts and have no authority.

Semantic visual review:

```json
{
  "candidate_id":"candidate-a",
  "passed":true,
  "evidence":["Every visible metric maps to the evidence packet"]
}
```

Reviewer fields use completed agent objects:

```json
{"agent_id":"...","status":"completed","result":"Read-only review result"}
```

Quality gate:

```json
{"name":"mise run validate","status":"passed","evidence":"exit 0"}
```

## Phase Validation

```text
python3 <skill-dir>/scripts/validate_run.py <manifest> --phase research
python3 <skill-dir>/scripts/validate_run.py <manifest> --phase plan
python3 <skill-dir>/scripts/validate_run.py <manifest> --phase orchestrate-preapproval
python3 <skill-dir>/scripts/validate_run.py <manifest> --phase implement
python3 <skill-dir>/scripts/validate_run.py <manifest> --phase review
```

The validator always performs remote GitHub read-backs. There is no live bypass. A nonzero exit code
blocks transition.

At run startup, capture the repository baseline before any phase work:

```bash
git rev-parse HEAD
git status --porcelain -- .github/specs
```

Store those exact values in `starting_commit` and `initial_spec_status`. The validator compares them
with the final repository state and committed diff. Also store a SHA-256 map of every file under
`.github/specs/**` in `initial_spec_hashes`, including already-dirty and untracked files.

Before each phase transition, spawn a fresh execution auditor with the bounded evidence packet and
append its completed receipt:

```json
{
  "phase":"research",
  "agent_id":"019f...",
  "status":"completed",
  "verdict":"PASS",
  "result":"Verified the supplied execution receipts and remote artifacts are consistent",
  "result_sha256":"SHA-256 of the auditor's exact completed response",
  "verified_event_ids":["tool-call-or-task-id"]
}
```
