# Phase Contracts

## Shared Rules

### Durable Artifact Chain

Each artifact must include:

- objective and scope;
- source evidence or links;
- assumptions and unresolved questions;
- backward link to its input;
- forward link when the next artifact exists;
- explicit non-goals;
- current status.

Required artifact types:

- Research: GitHub research issue.
- Plan: follow-up GitHub implementation issue with a validator-bound visual disposition and its
  required evidence. A final ImageGen mockup is attached or durably linked only for
  `generative_mockup`.
- Implement: branch, commit, and pull request linked to both issues.
- Review: dispositions and verification recorded on the pull request.

Do not create a repository spec. GitHub unavailability is a blocker, not permission to substitute a
local spec.

### Artifact Identity And Visual Grounding

Before a Plan or implementation transition, remotely read the candidate issue and confirm it is the
same workstream as its predecessor: problem, user/surface, decision, source/data contract, and
non-goals. A nearby issue about the same technology is not interchangeable authority. Record a
separate artifact chain for each initiative.

A visual disposition is derived from the complete scoped issue, acceptance criteria, tasks,
affected modules, intended paths, user direction, and repository evidence. `none` requires
positive complete nonvisual coverage; `runtime_capture` requires current, timestamped,
artifact-bound evidence covering every runtime scope ID but no
ImageGen; `generative_mockup` applies to new visual concepts and inherently visual deliverables
such as illustrations, icon sets, logos, artwork, photography, and brand or marketing assets, and requires the complete visual
tournament and publication contract.
Recompute it from intended paths at Implement Orientation and the actual diff at Review.

A normative visual for a user-facing change must be based on a current authenticated product capture
or an equally current executable reference. A concept visual based on an assumed shell must be
labelled concept-only and cannot satisfy Plan Exit Contract visual, matrix, or implementation-handoff
requirements.

### Evidence Hierarchy

Prefer, in order:

1. current executable behavior and tests;
2. current live runtime or product behavior;
3. current source and configuration;
4. current API, data, telemetry, and GitHub state;
5. repository documentation;
6. prior artifacts and conversation summaries.

Document material disagreements instead of silently choosing one source.

### Persistence, projection, and assurance contracts

Every tier uses [`context-capsule/v1`](context-capsule-v1.md). Its immutable CAS generations carry
the objective, settled decisions, source revisions, bounded evidence references, execution frontier,
unresolved questions, and next action even when Heavy assurance is pending or blocked.

Every projection transaction follows [`projection-bundle/v1`](projection-bundle-v1.md): freeze
authority bytes once, derive typed `present`, `omitted`, `pending`, or `blocked` slots, and preserve
the prepared digest while a separate `projection-transaction-receipt/v1` accumulates execution and
read-back evidence. Only `sealed` or `blocked` are final transaction states.

`assurance-policy/v1` keeps mode and assurance separate. Light stops at exactly six hard boundaries:
protected external writes, destructive or irreversible actions, production/release changes,
sensitive-data access, missing authority, and material architecture ambiguity. Unknown or
contradictory classification fails closed. Heavy preserves every current Deep gate.

### Judge Rubric

Adapt weights to the work, but score every candidate on the same dimensions:

- user or operator value;
- correctness and semantic integrity;
- fit with current product and architecture;
- usability and accessibility;
- implementation complexity;
- operational safety and rollout;
- testability and observability;
- privacy and security.

Judges must disclose decisive tradeoffs, not only totals.

## Research Exit Contract

Required:

- live behavior inspected when a live surface exists;
- current code and tests traced;
- live data or APIs queried read-only when relevant;
- metric/domain semantics defined;
- permissions, privacy, error, and freshness constraints recorded;
- existing reusable capabilities identified;
- overlapping backlog diagnosed;
- observed facts separated from inferences;
- detailed research artifact created;
- no solution selected and no product code changed.

If live access is unavailable, state the exact substitute evidence and confidence impact.

## Plan Exit Contract

Required:

- research revalidated;
- candidate implementation issue identity matches the research workstream; distinct adjacent
  initiatives remain in separate artifact chains;
- shared evidence packet frozen;
- non-negotiable constraints frozen before ideation;
- `plan-protocol/v2` runs have a valid event chain, canonical issue-body hash, deterministic Plan
  lint, and authenticated Plan-audit lineage;
- exactly three independent contestant subagents produced meaningfully different concepts;
- every submission includes its reasoned concept; ImageGen visuals are required only for
  `generative_mockup`;
- exactly two fresh, non-contestant judge subagents independently scored all complete submissions
  using one rubric;
- winning decision and both judge verdicts recorded;
- feasible differentiators from losing submissions merged into the winner, with concrete reasons
  for rejected differentiators;
- a dedicated synthesized final-winner ImageGen mockup exists when `generative_mockup` applies;
- synthesis confidence is recorded and is at least `7/10`;
- every sub-7 visual confidence result triggered another ImageGen iteration and reassessment when
  `generative_mockup` applies;
- the final mockup is normative when `generative_mockup` applies;
- acceptance criteria cover interactions and non-happy states;
- a disposition/accounting matrix maps every scope or applicable visual requirement to acceptance
  criteria, implementation tasks, and verification;
- feature-to-spec EARS patterns, personas, value assessment, diagrams, ambiguity handling, task
  structure, and validation rubric were applied;
- the complete plan is in a follow-up GitHub implementation issue, not `.github/specs/**`;
- architecture, contracts, bounds, privacy, rollout, ownership, and testing frozen;
- every task has an explicit structured `entry_gates` declaration, including `[]`, and every
  disposition is bound to an independent `dependency-classification/v1` Plan audit; every declared
  gate has a live-readback `dependency-readiness/v1` receipt bound to the exact Plan body;
  `READY` proves every gate, `PARTIAL_ONLY` names the exact executable complement plus transitive
  deferred closure and explicit user authorization, and `BLOCKED` prevents Plan exit;
- explicit out-of-scope list present;
- research and implementation issues cross-link each other;
- final mockup is attached or durably linked from the implementation issue when
  `generative_mockup` applies;
- stable tasks parse without ambiguity and `graph-policy/v1` is recomputed;
- `GRAPH_REQUIRED` has an exact current Plan draft plus remotely verified native graph;
- `NO_GRAPH` has a validator-recomputed receipt;
- external publication claims include successful mutation, remote read-back, durable URL/hash, and
  issue-body linkage; composer state or an attachment-only comment does not qualify;
- implementation invocation produced.

## Implement Orientation Contract

Before mutation, report:

- validation that every Plan Exit Contract item is satisfied by the GitHub implementation issue;
- authoritative artifact, current visual disposition, and applicable evidence;
- current branch/worktree cleanliness;
- plan-versus-current-code conflicts;
- frozen API/domain/component contracts;
- feature or rollout gating;
- protected external systems;
- worker ownership and dependency graph;
- structured dependency readiness re-read from the current remote Plan; an older `VALID` Plan receipt cannot
  bypass a missing, blocked, or body-drifted entry-gate receipt;
- approval and stop gates;
- required targeted, canonical, browser, CI, and stack checks;
- current capsule generation, prepared bundle identity, effective/achieved assurance, explicit slot
  omissions, and any blocked transaction receipt.

The implementation request authorizes ordinary scoped implementation and review-branch
publication. Wait only when the user requested a human stop or the next action crosses a named
hard boundary; a validator failure is repaired autonomously when the repair remains in scope.

## Implement Exit Contract

Required:

- shared contracts stabilized before parallel work;
- implementation workers have disjoint ownership plus explicit input, output, test, and handoff
  contracts;
- the main orchestrator owns integration and release accountability;
- integrated behavior matches the authoritative artifact;
- protected systems untouched unless separately approved;
- success and applicable non-happy states verified;
- browser/runtime checks completed for user-facing behavior;
- acceptance criteria mapped to tests;
- a fresh read-only test-coverage reviewer mapped every acceptance criterion and mockup row;
- a separate fresh read-only acceptance/visual reviewer compared the implementation with the
  normative mockup;
- the mockup-gap ledger contains zero unexplained differences;
- valid findings fixed and rechecked;
- repository quality gates pass;
- exact skips and residual risks named;
- focused commit and PR created;
- rollout and rollback documented;
- review invocation produced.

## Review Exit Contract

Required:

- all current review surfaces discovered;
- every substantive comment classified;
- every accepted claim verified;
- valid feedback covered by focused tests;
- invalid feedback rejected with evidence;
- affected quality gates rerun;
- remote check state reported;
- unresolved human decisions named;
- issue and PR status left accurate.
