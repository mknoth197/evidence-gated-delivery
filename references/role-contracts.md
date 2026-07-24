# Role Contracts

## Main Orchestrator

Owns:

- phase boundaries and human approval gates;
- shared evidence packet and frozen contracts;
- subagent prompts, ownership, and dependency order;
- ImageGen execution when workers cannot invoke it;
- tournament synthesis and confidence score;
- cross-worker integration and plan-versus-reality decisions;
- protected-system boundaries;
- final diff, quality gates, PR, and release accountability.

The orchestrator may delegate work but never delegates accountability.

## Tournament Contestants

Spawn exactly three independent contestant subagents.

Common inputs:

- same research issue and evidence packet;
- same production screenshots and design constraints;
- same non-negotiable product, data, access, privacy, and rollout boundaries;
- same scoring rubric.

Each returns:

- a distinct solution thesis;
- information architecture and interaction model;
- data/contract implications;
- important states and edge cases;
- tradeoffs and risks;
- an ImageGen-ready visual brief.

Contestants do not see or revise each other's submissions before judging.

The orchestrator completes each submission by generating an ImageGen visual from that contestant's
brief without changing its concept. The immutable judge packet for each contestant is:

```text
contestant concept + contestant visual brief + orchestrator-generated ImageGen visual
```

Judging cannot begin until all three packets are complete.

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
ordinary auditors against their local Codex subagent session record. In a `realtime_voice` parent
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

Inputs:

- GitHub implementation issue;
- mockup-accounting matrix;
- final diff;
- relevant tests and quality-gate configuration.

Returns:

- acceptance criterion to test mapping;
- mockup row to test mapping;
- uncovered success, failure, partial, privacy, accessibility, responsive, and rollout states;
- findings ordered by severity with file and line evidence;
- explicit statement of criteria with no meaningful coverage.

## Acceptance And Visual Reviewer

Fresh, read-only, and distinct from the test-coverage reviewer.

Inputs:

- normative final ImageGen mockup;
- implementation issue;
- production baseline;
- local desktop and mobile runtime;
- final diff.

Returns:

- mockup-gap ledger covering layout, hierarchy, copy, data mapping, interaction, states,
  accessibility, and responsive behavior;
- findings ordered by severity with evidence;
- unexplained differences requiring correction;
- confidence that implementation matches the normative target.

The orchestrator verifies findings, fixes valid gaps, and reruns the relevant reviewer after
material changes.
