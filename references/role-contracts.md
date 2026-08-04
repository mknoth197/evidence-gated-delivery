# Role Contracts

## Main Orchestrator

Owns:

- phase boundaries and human approval gates;
- shared evidence packet and frozen contracts;
- subagent prompts, ownership, and dependency order;
- visual-applicability evaluation and ImageGen execution only when `generative_mockup` applies;
- tournament synthesis and confidence score;
- cross-worker integration and plan-versus-reality decisions;
- protected-system boundaries;
- final diff, quality gates, PR, and release accountability.

The orchestrator may delegate work but never delegates accountability.

## Delegation Charter

Use this charter for every one-off task and subagent, regardless of tier:

```text
Objective: one independently completable outcome
Role: investigator | workstream owner | independent reviewer | required Deep role
Owned scope: files, systems, or question boundary
Inputs: frozen contract and evidence the child may rely on
Allowed actions: subset of the parent progress corridor
Completion evidence: concrete result, changed paths, checks, or read-back
Escalation conditions: only hard boundary, material scope expansion, or contradictory evidence
```

The child inherits the parent's authority corridor. It must continue ordinary dependent work and
return a bounded result to the orchestrator; it must not turn a local uncertainty into a new user
approval request. The orchestrator synthesizes completed results before starting another cohort.

## Tournament Contestants

Spawn exactly three independent contestant subagents.

Common inputs:

- same research issue and evidence packet;
- same production screenshots and design constraints;
- same non-negotiable product, data, access, privacy, and rollout boundaries;
- same scoring rubric.

Each always returns:

- a distinct solution thesis;
- information architecture and interaction model;
- data/contract implications;
- important states and edge cases;
- tradeoffs and risks;
- an ImageGen-ready visual brief only when `generative_mockup` applies.

Contestants do not see or revise each other's submissions before judging.

In `generative_mockup`, the orchestrator completes each submission by generating an ImageGen visual from that contestant's
brief without changing its concept. The immutable judge packet for each contestant is:

```text
contestant concept + contestant visual brief + orchestrator-generated ImageGen visual
```

In `none` or `runtime_capture`, the immutable judge packet is the concept plus applicable runtime
evidence, and image receipt arrays stay empty. Judging cannot begin until all three applicable
packets are complete.

## Tournament Judges

Spawn exactly two fresh judge subagents that did not compete.

Each receives:

- all three complete concept-plus-image submissions;
- the shared evidence packet;
- the same scoring rubric.

Each returns independently:

- per-dimension scores;
- total score and ranking;
- decisive strengths and risks;
- differentiating losing ideas worth salvaging;
- recommendation and confidence.

Do not reveal one judge's verdict to the other before both finish.

## Independent Plan Spec Auditor

Spawn fresh and read-only for preliminary audit, remediation recheck, and final remote-body audit.
Start the prompt with `Independent Plan spec auditor`. The auditor must be distinct from patch
authors, contestants, tournament judges, phase execution auditors, transition judges,
implementation workers, and every predecessor Plan auditor.
Use a persisted task name containing `plan` and `audit`, such as
`independent_plan_spec_auditor_final`.

Every receipt binds its collaboration-delegated parent/child trace, exact canonical issue-body
SHA-256, stable findings, predecessor lineage when applicable, timestamps, callback SHA-256, and
canonical receipt SHA-256. Blocker and High findings require a fresh verified recheck. Medium
findings require a verified patch or explicit owner, rationale, and accepted/deferred disposition.
The final audit independently classifies every task as `gated` or `none`, checks typed gate IDs
against the frozen task prose and authorities, and returns the exact body-bound
`DEPENDENCY-CLASSIFICATION:<sha256>:PASS` marker only when no external prerequisite is omitted.
Only a fresh `final_remote` receipt for the exact remotely read body may satisfy Plan.

## Execution Auditor

Spawn a fresh read-only execution auditor before every Research, Plan, Implement, or Review
transition. Give it the current manifest plus a bounded evidence packet containing exact tool-result
excerpts, task IDs, image paths and SHA-256 values, timestamps, and artifact URLs. It independently
reads remote GitHub artifacts and checks the packet for omissions, contradictions, duplicate IDs,
premature mutation, missing calls, and claimed-but-unverified external actions. For every claimed
publication, send it the action ledger entry with the mutation event, remote read-back event,
durable output, and state; it must fail the transition if any entry calls a started action verified.
It must not claim that fork context exposes the parent's raw
tool-event log. Start its prompt with `Execution auditor phase: <phase>`. Its result names the phase
and repeats every evidence ID it verified. It returns an explicit `PASS` or `FAIL`; only `PASS`
permits transition. Record the completed receipt, the SHA-256 of its exact final response, and its
evidence IDs in `trace_audits` before rerunning the phase validator. The validator authenticates
ordinary auditors against their local Codex subagent session record. When Codex Desktop
collaboration stores the assignment in the parent trace but not as a child `user` message, record
`receipt_kind: collaboration_delegated`, the UUID `agent_id`, exact `agent_path`, and `role_marker`.
The validator then requires the UUID-backed child metadata and completion to match the parent spawn
call, start event, completed callback, parent ID, agent path, timestamps, and result hash. A
manifest-only role assertion is insufficient. Because Desktop may encrypt the `message` argument in
the persisted parent trace, the unencrypted delegation `task_name` must use the exact
`execution_auditor_phase_<phase>` shape, such as `execution_auditor_phase_implement`. When the
message remains plaintext, the exact prompt marker also authenticates the role; an encrypted
message never permits a generic, mixed-phase, or cross-role task name to substitute.

In a `realtime_voice` parent
session, delegation agents do not receive standalone session files; record
`receipt_kind: realtime_delegated` and the exact delegated agent path instead. The validator then
authenticates the matching depth-one start event and completed callback against the UUID-backed
parent rollout session, validates the exact callback-payload hash and evidence IDs, and requires the role marker
to be recorded in the receipt. Generate that hash from the extracted callback, including any transport
completion prefix, rather than retyping or normalizing the auditor prose. This fallback is valid only for realtime parents; a caller with
task-inspection access still performs final prompt authentication after completion.

## Phase Retrospective Auditor

Spawn one fresh, read-only retrospective auditor after each completed Research, Plan, Implement,
and Review phase. It is independent from the phase execution auditor and does not judge product
quality subjectively. Give it the manifest, phase receipt, action ledger, remote artifact
read-backs, and the fixed rubric in `continuous-improvement.md`.

It returns one JSON-shaped result with:

- the phase and its agent identity;
- all seven fixed rubric scores from 0 through 4;
- a concrete evidence list for every score;
- observed strengths, failures, and root causes;
- a remediation action for every below-threshold or degraded result; and
- a recheck result when remediation is required.

The orchestrator records the result through `record_retrospective.py`. The auditor starts in the
background after phase completion, but no successor phase validates until the retrospective gate
passes. It compares against the recorder's baseline rather than inventing an ad hoc quality bar.

## Phase Transition Judge

Spawn one fresh, read-only Phase Transition Judge after each completed Research, Plan, and
Implement phase. It is distinct from the execution auditor, retrospective auditor, contestants,
tournament judges, implementation workers, and reviewers. It judges whether the completed phase is
technically accurate and safe to advance, not whether a proposed solution is aesthetically appealing.

Inputs are the frozen manifest and predecessor `VALID` receipt with SHA-256; execution-audit and
retrospective receipts; action ledger and durable artifact read-backs; and current source, tests,
runtime, and data evidence relevant to the successor. For Plan → Implement, inputs also include
the exact typed, live-readback `dependency-readiness/v1` receipt and its executable/deferred task sets. Missing or
`BLOCKED` readiness is a blocking finding; `PARTIAL_ONLY` may recommend proceed only for the exact
user-authorized task set and must preserve the deferred closure.

It returns `phase`, `successor_phase`, `agent_id`, `status` (`pass` or `fail`), `recommendation`
(`proceed` or `hold`), integer `confidence` (0–10), `technical_accuracy_score` (0–4),
`evidence_ids`, `blocking_findings`, `completed_at`, predecessor receipt SHA-256, and its exact
`result_sha256`. Each blocking finding names severity, claim, and evidence ID.

Only the validator derives `auto_proceed`: confidence must be at least 8, technical accuracy at
least 3, no high/critical unresolved finding, exact predecessor receipt binding, no open user stop,
and no hard-stop category. The judge must never claim that its score overrides these gates.

## Implementation Workers

Choose worker lanes after freezing the shared contract. Typical lanes are data/API, reusable UI
primitives, rollout/telemetry, and focused testing, but actual boundaries must follow the codebase.

Every worker prompt must state:

- owned files or modules;
- frozen interfaces it consumes or produces;
- required behavior and non-goals;
- tests and evidence it must return;
- handoff criteria;
- that other agents are working concurrently;
- that unrelated changes must not be reverted.

Workers implement only their bounded lane. The orchestrator integrates coupled behavior.

## Test-Coverage Reviewer

Fresh and read-only.
In encrypted Desktop collaboration runtimes, use
`test_coverage_reviewer` as the exact persisted task name and record
`receipt_kind: collaboration_delegated` plus `agent_path`; validation binds the parent spawn,
child UUID, callback, timestamps, exact callback SHA-256, and task-name marker. The manifest
`result` must equal the authenticated callback, and `started_at`/`completed_at` must equal the
authenticated delegation and child-completion timestamps. A transition-judge task name, generic
review task name, self-attestation, or UUID-only receipt cannot satisfy this role.

Inputs:

- GitHub implementation issue;
- mockup/disposition-accounting matrix;
- final diff;
- relevant tests and quality-gate configuration.

Returns:

- acceptance criterion to test mapping;
- accounting row to test mapping;
- uncovered success, failure, partial, privacy, accessibility, responsive, and rollout states;
- findings ordered by severity with file and line evidence;
- explicit statement of criteria with no meaningful coverage.

## Acceptance And Visual Reviewer

Fresh, read-only, and distinct from the test-coverage reviewer.
Run this role only when `runtime_capture` or `generative_mockup` applies.

Inputs:

- current runtime evidence for `runtime_capture` or normative final ImageGen mockup for
  `generative_mockup`;
- implementation issue;
- production baseline;
- local desktop and mobile runtime;
- final diff.

Returns:

- visual-gap ledger covering layout, hierarchy, copy, data mapping, interaction, states,
  accessibility, and responsive behavior;
- findings ordered by severity with evidence;
- unexplained differences requiring correction;
- confidence that implementation matches the normative target.

The orchestrator verifies findings, fixes valid gaps, and reruns the relevant reviewer after
material changes.
