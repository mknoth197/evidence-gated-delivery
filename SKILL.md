---
name: evidence-gated-delivery
description: Run complex product or engineering work as a reusable Research, Plan, Implement, and Review artifact chain. Use when the user supplies a goal, research issue, implementation issue, or pull request and wants evidence-backed discovery, competing plans, visual ideation, contract-first subagent execution, independent review, or safe PR follow-through. Trigger on explicit `$evidence-gated-delivery` invocation and natural-language requests such as "run my evaluation loop", "use my RPI loop", "research-plan-implement this", "run the design tournament", "use the workflow from my previous threads", or "run my agentic engineering playbook". Do not use for small direct fixes that do not justify phase separation.
---

# Evidence-Gated Delivery

## Purpose

Select a proportionate delivery tier before operating a state machine:

```text
Quick: targeted evidence -> scoped change -> targeted check
Balanced: concise contract -> evidence -> implementation -> verification
Deep: RESEARCH -> research artifact -> PLAN -> implementation artifact -> IMPLEMENT -> PR -> REVIEW
```

Deep operates the durable state machine:

```text
RESEARCH -> research artifact -> PLAN -> implementation artifact -> IMPLEMENT -> PR -> REVIEW
```

Every phase must revalidate its input against current reality. Durable artifacts, not chat memory,
carry authority between phases.

Use one coherent workflow release per run. When operating from this repository, run
`scripts/verify_skill_sync.py --installed-skill <active-skill-dir>` before a Deep mutation if an
installed copy exists. If it reports drift, do not mix scripts from the checkout and installed
copy: execute from the current repository release, record the drift, and treat distribution repair
as ordinary recoverable work. Never classify remote state as a conflict using a stale reconciler.

Use `scripts/intent_router.py` internally to assess scope, ambiguity, reversibility, data risk,
novelty, external impact, and hard-stop signals. A user may request a higher tier, but never has
to invoke a tier, provide a routing file, or repeat an approval in a prescribed phrase.
Protected external writes, destructive or irreversible actions, production or release actions,
sensitive-data access, missing authority, and material architecture ambiguity always select Deep
and require explicit authority or resolution. For ordinary scoped work, direct user intent implies
authority to inspect, edit, test, reconcile, repair, create a branch and commits, publish or update
a review branch, pull request, or scoped issue, and verify those actions through read-back. Treat
this as a **progress corridor**: batch low-risk, reversible actions and keep moving until a proposed
action crosses a named hard boundary or materially expands scope. Do not turn a discovered
ordinary sub-step or validator gate into a new approval question.

Maintain one **execution frontier**: the next material action, its recovery state (`continue`,
`repair`, `escalate`, or `retire`), and the evidence delta expected from it. Log a progress event
after each meaningful action. Repeated non-progress with no state or evidence delta is a stall:
execute the smallest safe repair, name one real hard boundary, or retire the path. Do not create
another audit merely to restate the stall. Use `scripts/progress_control.py` internally to expose
the tier evidence budget, gate accountability, and outcome signals.

For Deep, `orchestrate` proceeds autonomously from Research through Plan and Implementation when
every deterministic gate passes and a fresh independent Phase Transition Judge rates the completed
phase at least `8/10` for confidence and `3/4` for technical accuracy. A user may request a stop
before `plan`, `implement`, or `review` at any time; that explicit stop wins over automation until
the user releases it. Protected external writes, destructive or irreversible actions, production
or release actions, sensitive-data access, missing authority, and material architecture ambiguity
remain hard stops and are never bypassed by a score.

Read [phase-contracts.md](references/phase-contracts.md) and
[run-manifest.md](references/run-manifest.md) when beginning a run. Read only the active phase plus
Shared Rules from the phase contracts. Read [role-contracts.md](references/role-contracts.md) before
spawning tournament, implementation, or review subagents.
Read [plan-protocol.md](references/plan-protocol.md) before Plan, Implement Orientation, or graph
operations.
Read [artifact-publication.md](references/artifact-publication.md) before Plan publication.
Read [continuous-improvement.md](references/continuous-improvement.md) before beginning any run.

Create a run manifest before substantive **Deep** work. Quick and Balanced work may keep a compact
internal evidence note; create a manifest only when durable handoff or a validator receipt is
actually useful. The router is an internal decision aid, not a user-facing ceremony:

```text
python3 <skill-dir>/scripts/init_run.py --mode <mode> --goal "<goal>" --repo <repo-root>
```

Before any phase transition or final answer, run:

```text
python3 <skill-dir>/scripts/validate_run.py <manifest.json> --phase <phase>
```

If validation fails, do not transition and do not send a success-style final answer. Continue the
missing work or report the concrete blocker.

The validator writes a machine-readable receipt under
`/tmp/evidence-gated-delivery-receipts/<run-id>/<phase>.json`. Never claim `VALID` without that file.

## Invocation

Mode and assurance are separate axes. Parse an optional assurance selector exactly once and only
immediately after the skill name:

```text
$evidence-gated-delivery [--assurance light|heavy] <research|plan|implement|review|orchestrate> <authority-or-goal>
```

`status` rejects the selector because it creates no run. A missing value, duplicate or unknown
selector, later-position selector, unsupported mode, or extra positional assurance token emits
`BLOCKED_ASSURANCE_SELECTION`; never infer a downgrade. Existing phase and inferred invocations
remain Heavy. Legacy `quick` and `balanced` map to Light while retaining their subprofile gates;
legacy `deep` maps exactly to Heavy.

The user can also invoke the skill with only a goal or artifact:

```text
$evidence-gated-delivery Add self-service deployment health insights
$evidence-gated-delivery quick Correct a local validation edge case
$evidence-gated-delivery balanced Add a bounded integration with tests
$evidence-gated-delivery orchestrate Redesign My Brief for consumer comprehension
$evidence-gated-delivery https://github.com/org/repo/issues/123
$evidence-gated-delivery continue https://github.com/org/repo/issues/124
$evidence-gated-delivery review https://github.com/org/repo/pull/125
$evidence-gated-delivery status https://github.com/org/repo/issues/124
```

For Deep handoffs, print the next invocation. Do not make Quick or Balanced users copy commands to
continue ordinary work.

## Mandatory Startup Declaration

The first commentary update after activation must state:

```text
Mode: <quick|balanced|research|plan|implement|review|status|orchestrate>
Authority: <goal or artifact URL>
Expected durable artifact: <GitHub issue|GitHub implementation issue|PR|review dispositions>
Next valid stop: <named exit contract or approval gate>
```

Do not describe the run vaguely as "research and design guidance."

## Mode Resolution

First resolve the optional leading `--assurance light|heavy` selector, then parse the mode. Persist
`mode`, `requested_assurance`, `requested_legacy_tier`, `effective_assurance`,
`legacy_subprofile`, `selection_origin`, and `achieved_assurance` for every created run.

Parse an explicit mode when present:

- `research`: run Research.
- `plan`: run Plan.
- `implement`: run Implement.
- `review`: run Review.
- `status`: report current state and the next valid transition without mutation.
- `continue`: inspect the artifact and infer its next phase.
- `quick`: run the Quick contract.
- `balanced`: run the Balanced contract.
- `deep`: alias for `orchestrate` and its complete artifact chain.
- `orchestrate`: run the Deep convenience loop described below.

When no mode is explicit:

1. Explicit `quick`, `balanced`, or `deep` selects that tier subject to a hard-risk floor; map
   `deep` to `orchestrate`.
2. Natural-language request to "run", "use", "repeat", or "try" the evaluation loop, RPI loop,
   design tournament workflow, previous-thread workflow, or agentic engineering playbook ->
   `orchestrate`.
3. A bounded direct fix with low router score -> `quick`.
4. Other freeform goal or feature request -> router-selected `balanced` or `deep`.
5. GitHub issue dominated by findings, observations, unknowns, or research context -> `plan`.
6. GitHub issue containing frozen acceptance criteria, design, tasks, rollout, or implementation
   contracts -> `implement`.
7. Pull request URL or number -> `review`.
8. Ambiguous artifact -> inspect linked artifacts and repository state, then choose the earliest
   incomplete phase. Ask only if choosing incorrectly would cause mutation or scope expansion.

State the selected mode and why in one sentence before beginning.

## Shared Rules

### Evidence

Triangulate the sources relevant to the work:

- live product or runtime behavior;
- current source, tests, instructions, and history;
- live data, APIs, telemetry, or infrastructure in read-only mode;
- current GitHub issues, pull requests, checks, and discussions.

Separate observed facts, supported inferences, and unresolved questions.

### Artifacts

- Research ends in a linked GitHub research issue.
- Plan ends in a linked GitHub implementation issue with testable acceptance criteria and a
  validator-bound visual disposition. A normative mockup is attached or durably linked only when
  the disposition selects `generative_mockup`.
- Implement ends in a focused branch and pull request.
- Review ends in verified dispositions, fixes where valid, and current check status.
- Link each artifact backward and forward.

This workflow uses GitHub Issues as the Research and Plan artifact system. Do not create, save, or
commit a repository spec. If GitHub issue creation is unavailable, stop and report the blocker
instead of silently substituting a local file or chat response.

### Traceable Claims

- Do not claim that a subagent, contestant, judge, or reviewer ran without a real spawn result and
  completed result in the task trace.
- Record every spawned agent ID in the run manifest.
- Do not claim that judges agreed without two completed, independently returned verdicts.
- Do not claim that an ImageGen visual exists without an ImageGen tool result and saved path.
- Do not claim that a GitHub artifact exists without its returned URL and a remote read-back.
- Do not transition based on hidden reasoning that substitutes for a required tool call.
- Treat the JSON validator as a structural and remote-artifact gate, not as proof of Codex tool
  provenance. Before every phase transition, give a fresh independent execution auditor the
  manifest plus exact tool-result excerpts, task IDs, image paths and hashes, and artifact URLs.
  The auditor checks internal consistency and independently reads remote artifacts; it must not
  claim access to an unavailable parent trace. Record its completed receipt and verified evidence
  IDs, then rerun the validator. After the task completes, true trace authentication is performed
  by an external task inspector that can read the completed task transcript.

### Claimed External Actions

Use a two-state vocabulary for every external action: **started** means the tool invocation was
issued; **verified** means its required observable result was independently read back. Never use
`attached`, `published`, `updated`, `sent`, `created`, `complete`, or equivalent past-tense success
language for a started-only action. A failed tool call, a staged browser composer, or an optimistic
UI message is not evidence of completion.

Before reporting an external action as verified, record all of: (1) target URL or identifier,
(2) successful mutation receipt, (3) remote read-back evidence, and (4) the durable value the next
phase consumes (for example a URL, revision, attachment hash, comment ID, or check run). If any is
missing, report the precise pending state and the next repair action, without a completion marker.

Keep the action ledger in the manifest evidence packet or auditor packet. A compact entry is
`action / target / started-event / verified-event / durable-output / state`. The execution auditor
must reject a verified claim whose ledger entry is incomplete.

### Workstream Identity

Before mutating or treating an issue as authoritative, freeze a workstream identity: problem name,
intended user/surface, decision to be made, source/data contract, and artifact URLs. Read the issue
body remotely and compare it with that identity. A request for a distinct initiative (for example,
AI-experiment discovery versus a My Brief enhancement) is a separate workstream even if both touch
agentic capabilities. Do not retrofit, relabel, or advance the other workstream's issue; create or
resume the matching artifact chain instead.

### Agent = Model + Harness

Treat repository instructions, domain skills, live tools, tests, linters, CI, browser checks,
judges, and review comments as the harness. Prefer executable evidence over prose claims.

### Continuous Improvement

After every completed Research, Plan, Implement, and Review phase, spawn one fresh independent
retrospective auditor in the background. Give it the frozen evidence packet, action ledger,
validator receipt, durable artifacts, and the fixed rubric in `continuous-improvement.md`.
Record its result through `record_retrospective.py`. The next phase may prepare read-only work in
parallel, but cannot validate or transition until the predecessor retrospective is completed.
Treat a score below 85/100, either evidence/verification dimension below 3/4, or a five-point
regression from the stored baseline as a remediation gate. Record and recheck remediation; never
reduce the threshold to make a run appear healthy.

### Autonomous Transitions And Human Stops

At each Research → Plan, Plan → Implement, and Implement → Review boundary, dispatch one fresh
read-only **Phase Transition Judge** after the predecessor's execution audit and retrospective.
The judge is independent from those auditors and receives the frozen manifest, predecessor `VALID`
receipt SHA-256, action ledger, relevant artifact read-backs, and current code/runtime/test evidence.
It must return a structured technical-accuracy assessment, evidence IDs, blocking findings, a
confidence integer from 0 through 10, and a `proceed` or `hold` recommendation.

The validator may authorize `auto_proceed` only at confidence `8..10`, technical accuracy `3/4` or
higher, no unresolved high or critical findings, no hard stop, and a decision bound to the judge's
exact response hash. Confidence is judgment evidence, never a substitute for the validator,
execution audit, retrospective, repository instructions, or protected-system authority.

Default `automation_policy` is autonomous with `stop_before_phases: []`. On a user request such as
“stop before Implementation”, add `implement` to that list and report the completed predecessor
with a `human_stop` decision; do not start the successor. Resume only after a new explicit user
instruction is recorded in `released_stop_gates` with its timestamp and exact evidence. Interpret
`implementation` as `implement`; do not infer a release from silence or a previous general request.

### Parallelism

The orchestrator owns the goal, authority envelope, integration, and user communication in every
tier. Threads are a core capability, not a Deep-only ceremony. Use
`scripts/delegation_router.py` internally to select `solo`, `one_off`,
`parallel_workstreams`, or `phase_isolated` work.

- Spawn a thread only for an independent outcome that repays coordination cost: a slow or external
  investigation, an isolated workstream, or an independent challenge.
- Quick defaults to solo; use one one-off investigator only for a genuinely independent wait or
  question. Balanced may fan out up to three disjoint workstreams. Deep uses phase-isolated work
  and its required independent roles.
- Every child receives a Delegation Charter: objective, role, owned scope, inputs, inherited
  allowed actions, completion evidence, and escalation conditions. It inherits the parent's
  progress corridor and cannot ask the user to re-authorize ordinary dependent work.
- Freeze shared contracts before parallel writes. Tell workers that other agents may edit
  concurrently and not to revert unrelated changes.
- Synthesize a completed cohort into the next material action before spawning another cohort. Do
  not re-delegate an answered question unless new evidence invalidates its answer.
- Keep coupled integration with one accountable orchestrator. Use fresh read-only agents for
  independent challenge and final review; treat reviewer comments as hypotheses and verify them.

### Safety

- Classify the action, not merely whether it writes. Ordinary scoped, recoverable external writes
  such as a review branch, pull request, issue, comment, or exact graph publication remain inside
  the progress corridor and require immediate read-back.
- Pause only for a named hard boundary: protected external writes; destructive or irreversible
  actions; production or release actions; sensitive-data access; missing authority; or material
  architecture ambiguity that changes a public contract, persistence model, security boundary, or
  irreversible migration. An unknown or contradictory risk classification fails closed.
- Never infer authority to merge, deploy, release, mutate production flags or cloud resources,
  change credentials or accounts, access sensitive data, delete data, or force-overwrite history.
- A validator is an evidence gate, not automatically a permission gate. Never bypass it, but repair
  ordinary failures autonomously when the repair remains inside the authority envelope.
- Honor repository-specific approval gates and quality commands.
- Do not weaken tests or acceptance criteria merely to make a gate pass.

## Quick Contract

Use Quick only for a low-risk, bounded, reversible request. Do not create GitHub artifacts,
manifests, routing files, or a human checkpoint merely because the workflow is active.

1. State the selected tier and the bounded outcome.
2. Inspect the relevant current source, test, or runtime evidence.
3. Perform the ordinary scoped work authorized by the request.
4. Run the smallest meaningful targeted check.
5. Report the source and check concisely; claim an external action only after read-back.
6. Use `validate_tier.py` only when a durable receipt is needed for handoff or dispute.

Escalate to Balanced or Deep when discovery invalidates the routing evidence. Stop for explicit
authority only when an action crosses the recorded authority envelope.

## Balanced Contract

Use Balanced for moderate uncertainty, multiple surfaces, or a requested higher-confidence pass.
It is not a shortened Deep run: it does not require a design tournament or paired GitHub issues.

1. State a concise contract in the working response: objective, bounded scope, non-goals, and verification.
2. Inspect at least two current independent sources relevant to the change.
3. Perform the ordinary scoped work authorized by the request and verify claimed external actions
   through read-back.
4. Run targeted checks and an independent review when the work touches an interface or has a
   meaningful regression risk.
5. Summarize the contract, sources, checks, and any external-action read-back at handoff.
6. Use `validate_tier.py` only when a durable receipt is needed for handoff or dispute.

Escalate to Deep when a hard-stop signal, material ambiguity, or a new product/architecture
decision emerges. A temporary failure, a recoverable conflict, or a newly discovered ordinary
dependent step is not by itself an escalation or a reason to pause.

## Research Mode

Goal: establish what is true without selecting or implementing a solution.

1. Freeze the research question and explicit non-goals.
2. Read applicable instructions and discover relevant skills/tools.
3. Inspect live behavior, source, and live data independently.
4. Trace data lineage, metric semantics, permissions, errors, freshness, and existing capabilities.
5. Search current GitHub issues and pull requests for duplicates, adjacent work, active delivery,
   and stale assumptions before creating the research issue.
6. Test suspected stale issues as hypotheses before closing or modifying them.
7. Diagnose how the new goal relates to current issues and PRs. Link rather than silently duplicate.
8. Create a detailed GitHub research issue with the exact headings `## Evidence`,
   `## Observed Facts`, `## Inferences`, and `## Unresolved Questions`, then read it back remotely.
9. Do not prescribe architecture or edit product code.

Exit only when the Research Exit Contract in the reference is satisfied.

Next invocation:

```text
$evidence-gated-delivery plan <research-artifact-url>
```

## Plan Mode

Goal: choose and freeze what should be built.

1. Revalidate the research artifact against current product, source, data, and rollout mechanisms.
   For a new transactional run, read the authoritative implementation issue body once as immutable
   bytes and run the versioned task, graph-policy, graph-draft, visual-disposition,
   Plan-audit-input, and preflight adapters through the projection kernel. Persist the prepared
   bundle and separate transaction receipt in `projection_transaction_evidence`; every adapter
   must bind the same input digest. Later graph mutations stage intent and append normalized
   read-back evidence without rebuilding or mutating the prepared bundle.
   Before screenshots, visual briefs, or ImageGen, evaluate `visual-applicability/v1` from the
   complete scoped deliverable, effective user direction, acceptance criteria, tasks, affected
   modules, intended paths, and repository evidence. Select:
   - `none` for complete, positively proven nonvisual scope;
   - `runtime_capture` for existing-UI work fully verifiable through current runtime, DOM,
     accessibility, or visual-regression evidence; or
   - `generative_mockup` for new or substantially redesigned visual concepts, inherently visual
     deliverables such as illustrations, icon sets, logos, artwork, photography, brand or
     marketing assets, or explicit ImageGen exploration.
   An unrelated frontend does not make images applicable. Documentation expressed adequately
   through prose, tables, or Mermaid is nonvisual unless appearance is itself the deliverable.
   Incomplete or materially ambiguous scope blocks before visual work. Bind the canonical scope
   inventory and remote issue-body hashes and recompute at Implement Orientation and Review.
   Persist the exact effective user-direction text in `visual_user_directions`; the receipt must
   match its hashes, directives, authority, scope, source order, and turn rather than synthesizing
   a neutral direction.
   Runtime sufficiency defaults false. Select `runtime_capture` only when every runtime-classified
   scope entry is covered by a current evidence record naming its scope IDs, allowed capture kind,
   timezone-aware capture timestamp, bounded evidence, readable absolute local artifact path, and SHA-256
   matching artifact bytes whose content signature is valid for the declared capture kind.
   Evidence older than the run freshness bound, more than five minutes ahead of validation time,
   unscoped prose, an unreadable or type-mismatched artifact, or a bare `{kind, evidence}`
   assertion is insufficient. Recompute these fields from
   the authoritative evidence during validation; never reuse embedded sufficiency booleans.
   Screenshot and visual-regression evidence currently requires a structurally valid,
   non-interlaced PNG with bounded compressed/decompressed size, valid critical chunks, CRCs, and
   reconstructed scanlines, including conforming indexed-color palette rules; unsupported
   recording containers and unvalidated ancillary PNG chunks fail closed.
   If any deliverable cannot be positively classified as visual or nonvisual, block for
   bounded clarification instead of defaulting to `none`. Classify the created object's head noun
   separately from modifier or surrounding API, service, workflow, or documentation context.
   An otherwise unknown goal may resolve to `none` only when its exact bound tasks and affected
   modules provide complete positive nonvisual coverage. Ambiguity in a deliverable, task, or
   acceptance criterion survives and blocks.
   Capture the current authenticated product surface only when the selected mode requires it.
   An assumed shell may illustrate a research concept, but it is not eligible to be normative or to
   enter the mockup-accounting matrix.
2. Freeze one shared evidence packet and non-negotiable constraints before ideation.
3. Act as the main orchestrator. Delegate exactly three meaningfully different approaches to three
   independent contestant subagents using the same evidence packet and rubric.
4. Require every contestant to provide a reasoned concept. Only in `generative_mockup`, require an
   ImageGen-ready visual brief grounded in current product screenshots and design constraints.
5. Only in `generative_mockup`, use ImageGen to create one visual for every contestant. If a
   contestant cannot invoke ImageGen,
   the orchestrator must generate its visual from that contestant's brief without changing the
   concept.
   Before judging, audit every visible metric, label, freshness claim, causal statement, and
   comparison in each visual against the evidence packet. Unsupported semantics invalidate the
   packet and require corrected ImageGen output. Ground every visual brief and final mockup in a
   current captured product shell recorded in visual_grounding; do not infer the shell from memory
   or use a generic dashboard.
   In `runtime_capture` and `none`, retain empty contestant-image and semantic-visual-review arrays.
6. Spawn exactly two fresh judge subagents that did not compete. Give each judge all three complete
   concept submissions plus the evidence required by the selected visual mode and the same scoring
   rubric. Judges score independently and do
   not see each other's verdict before submitting.
   Record both agent IDs, complete verdicts, scorecards, and confidence in the run manifest.
7. Aggregate the two verdicts and select the winner. Cherry-pick differentiating ideas from losing
   submissions into the winner whenever feasible. Reject one only with a concrete incompatibility,
   semantic risk, or documented tradeoff.
8. Record `synthesis_confidence` from 1-10 in every mode. In `generative_mockup`, use ImageGen
   again to create a dedicated final-winner mockup. If visual confidence is below `7/10`, revise,
   regenerate, and reassess.
9. In `generative_mockup`, treat the final mockup as normative. Every visible element, interaction,
    state, label, data
    mapping, responsive rule, and accessibility behavior must be represented in the plan. Require
    no differences or gaps unless a deviation is explicitly documented and approved.
10. Read the bundled [`feature-to-spec` skill](bundled-skills/feature-to-spec/SKILL.md) for EARS
    patterns, personas, value assessment, diagrams, ambiguity handling, task structure, and
    validation rubric. Apply repository-specific spec instructions as additional constraints when
    present. Do not execute its repository-file creation phase.
11. Redirect the complete feature-to-spec-quality output into a follow-up GitHub implementation
    issue. It must use the exact headings `## Problem Statement`, `## Personas`,
    `## Value Assessment`, `## User Stories`, `## Design`, `## Tasks`, `## Out of Scope`,
    `## Acceptance Criteria`, `## Mockup Accounting Matrix`, and `## Cross-Reference`. Include the
    final image SHA-256 and durable URL only in `generative_mockup`; otherwise publish the
    validator-bound disposition matrix. Never write `.github/specs/**` or another repository spec.
12. Include a disposition/accounting matrix mapping every scope or applicable visual requirement
    to acceptance criteria, implementation tasks, and planned verification.
13. Run deterministic Plan lint and a fresh independent `plan-auditor` session against the frozen
    body. Block on Blocker/High findings; require a fresh independent remediation recheck. Patch or
    explicitly disposition every Medium with an owner and rationale.
14. Freeze contracts, architecture, states, bounds, privacy, rollout, subagent ownership, tests,
    out-of-scope behavior, and rollback.
15. Follow the publication transaction in [artifact-publication.md](references/artifact-publication.md):
    preflight the issue body, publish the issue, attach or durably host the final mockup only in
    `generative_mockup`, update both
    issue bodies with reciprocal URLs, then read both back remotely.
    Run the local preflight before issue creation or completion:

    ```text
    python3 <skill-dir>/scripts/preflight_plan.py <issue-body.md> \
      --visual-disposition <visual-disposition.json> [--final-image <mockup.png>]
    ```

    For a private GitHub repository attachment, also pass
    `--github-issue-url https://github.com/<owner>/<repo>/issues/<number>`. The preflight resolves
    the durable `github.com/user-attachments/...` URL through authenticated rendered-issue
    read-back and still requires the fetched bytes to match the local image SHA-256.
16. Read the final issue body back, bind its canonical hash, and obtain a fresh independent
    `final_remote` Plan audit for those exact bytes.
17. Parse the stable task grammar and recompute `graph-policy/v1`. For `NO_GRAPH`, retain the
    validator receipt and reject any graph lifecycle or mutation event as contradictory evidence.
    For `GRAPH_REQUIRED`, use bundled `plan-to-graph` to freeze an exact draft
    and automatically publish it when its current identity, capability, and collision preconditions
    hold. Publish one item at a time and verify the complete native graph remotely.
18. Run the Plan execution auditor and validator only after all disposition, audit, task, graph,
    acceptance, matrix, and reciprocal-link evidence survives remote publication.
19. In `generative_mockup`, if durable mockup publication fails, keep the Plan issue explicitly
    marked blocked; do not
    claim Plan completion or an implementation approval gate, and print the repair invocation.
20. Produce a compact implementation invocation only after a `VALID` Plan receipt; do not require
    the user to paste the harness.

Exit only when the Plan Exit Contract in the reference is satisfied.

Next invocation:

```text
$evidence-gated-delivery implement <implementation-artifact-url>
```

## Implement Mode

Goal: implement the frozen contract and prove the outcome.

### Orientation Gate

Before mutation:

1. Validate the GitHub implementation issue against every item in the Plan Exit Contract.
   Recompute visual applicability from the approved tasks and intended changed paths. Confirm its
   linked research issue, feature-to-spec-quality content, disposition-required visual evidence,
   accounting matrix, cross-links, and GitHub-issue-only authority. If any item is missing,
   stop and return to Plan Mode to repair the issue before implementation.
2. Reconcile the implementation artifact with current code, tests, runtime, data, and external
   configuration.
3. Freeze request/response contracts, domain semantics, bounds, targeting, privacy, component
   interfaces, ownership, dependency order, and quality gates.
4. Identify plan-versus-reality conflicts and resolve them explicitly.
5. Present the frozen contract, worker map, stop gates, and protected external systems.
6. With the default autonomous policy, begin internal implementation only after the validated Plan
   transition judgment authorizes `auto_proceed`. If a user stop gate is open, stop before any
   successor mutation. Do not use autonomous transition authority for protected external writes,
   destructive or irreversible actions, production or release changes, or a missing authority.

An autonomous implementation transition does not imply approval to mutate protected external systems.

### Execution

1. Create or switch to the repository-conformant branch.
2. Claim/update tracked work according to repository instructions.
3. Stabilize and compile the shared contract first.
4. Delegate independent foundations after the contract is stable. Give each implementation
   subagent explicit inputs, owned files/modules, output contract, tests, and handoff criteria.
5. Integrate worker output continuously; challenge it against the contract.
6. Keep final user-facing integration, cross-worker decisions, and release accountability with the
   main orchestrator.
7. Use live browser/runtime verification throughout, not only after tests.
8. Exercise success, zero, unavailable, partial, loading, truncation, privacy, accessibility, and
   responsive states as applicable.
9. Run a fresh read-only test-coverage reviewer that maps every GitHub issue acceptance criterion
   and accounting row to meaningful coverage.
10. Recompute visual applicability from the actual diff and runtime surfaces. Run a separate
    acceptance/visual reviewer only for `runtime_capture` or `generative_mockup`.
11. When a visual mode applies, maintain a visual-gap ledger. Fix every unexplained difference;
    document and obtain approval for any intentional deviation. Completion requires zero
    unexplained gaps.
12. Verify every reviewer finding before fixing it; rerun the relevant reviewer after material
    changes.
13. Run targeted checks, canonical validation, CI mirror, and stack checks required by the repo.
14. Audit the final diff, commit, push, and open a focused PR with exact evidence and skips.

Next invocation:

```text
$evidence-gated-delivery review <pull-request-url>
```

## Review Mode

Goal: shepherd the pull request using current evidence.

1. Inspect the current branch, diff, checks, reviews, inline threads, and issue acceptance criteria.
2. Use any repository-mandated review-triage skill.
3. Classify every comment as valid, valid differently, invalid, already fixed, or needs user input.
4. Verify claims against reachable code and runtime behavior.
5. Implement only verified feedback, with focused regression coverage.
6. Resolve or reply with concise evidence.
7. Rerun affected gates and report current remote checks.
8. Continue monitoring only when the user requests a watcher, heartbeat, or loop.

Do not equate green CI with proof that review claims are false. Do not equate reviewer confidence
with proof that claims are true.

## Status Mode

Read only. Report:

- detected phase and artifact chain;
- completed exit contracts;
- unmet gates;
- current blockers or protected writes awaiting approval;
- exact next invocation.

Do not update issues, branches, files, PRs, or external systems.

## Orchestrate Mode

Use when Mode Resolution selects `orchestrate`, whether from an explicit invocation or an
unambiguous natural-language request to run the complete loop:

```text
$evidence-gated-delivery orchestrate <goal>
```

This is a convenience loop that invokes the same modes without weakening them:

1. Execute Research Mode and satisfy the complete Research Exit Contract before transition.
2. Execute Plan Mode and satisfy the complete Plan Exit Contract before transition, including the
   tournament, visual disposition, applicable evidence mode, confidence, feature-to-spec
   redirection, Plan audit, graph policy, GitHub issue, and accounting requirements.
3. Execute the complete Implement Orientation Contract and honor any user-requested stop or named
   hard boundary.
4. Otherwise continue into Implement Mode and satisfy the complete Implement Exit Contract.
5. Open the PR and report the Review invocation.

Even in orchestrate mode:

- do not skip durable artifacts;
- do not let contestants judge themselves;
- do not skip contestant or final-winner ImageGen when `generative_mockup` applies;
- do not generate images in `none` or `runtime_capture`;
- do not advance with synthesis confidence below `7/10`;
- do not begin implementation before the orientation gate;
- do not perform protected external writes without separate approval;
- do not collapse review into automatic acceptance of comments.

Validate `research` before entering Plan, validate `plan` before entering Implement Orientation,
and validate `orchestrate-preapproval` before requesting approval. A recommendation, selected
winner, concept board, or chat summary is never a valid stopping artifact.

Prefer phase-isolated tasks for high-risk, large, or politically sensitive work because a fresh
task reduces anchoring and context contamination.

## Completion Output

Every response that completes a phase must include:

- artifact created or advanced;
- evidence gathered;
- gates passed and skipped;
- unresolved risks or protected writes;
- exact next invocation in a fenced text block.

Also include the validator receipt path and its `VALID` result. For orchestrated runs, include the
fresh independent execution-audit verdict and state that external transcript authentication remains
for the caller. Never end a phase on an unvalidated manifest or execution audit.

When validation is `INVALID`, use the words `blocked before <next phase>` and do not use
`completed`, `published the Plan`, `stopped at the approval gate`, or equivalent success wording.
