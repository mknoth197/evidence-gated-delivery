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
  "selected_winner": "",
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

## Initiative identity, visual grounding, and external actions

For Plan and later phases, initiative_identity binds exactly one name and slug to the research and
implementation issue URLs. Both URL values must exactly match the manifest fields. A newly discovered
product surface or technology experiment starts a separate Research run and cannot reuse this Plan,
tournament, mockup, or approval gate.

visual_grounding records at least one current product-shell observation used by the mockups: surface,
live URL, existing screenshot path, its SHA-256, observation time, and source components. A remembered
or generic dashboard shell is not evidence.

external_actions records final-mockup-publication, plan-issue-readback, and research-issue-readback.
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

Final iterations use the same shape plus numeric `confidence`. Every path and call ID must be
distinct across candidate and final images. `final_image_url` must be a durable HTTP(S) URL included
in the published implementation issue.

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
