# Evidence-Gated Delivery

An auditable, tiered workflow for agentic engineering work.

It treats agents as **model + harness**: the model generates and reasons, while durable artifacts,
tests, independent review, validator receipts, and explicit stop gates make outcomes trustworthy.

## What it enforces

- Evidence-backed research before architecture selection.
- A three-concept design tournament with two independent judges.
- A deterministic visual-applicability gate: no images for proven nonvisual work, runtime evidence
  for bounded existing-UI changes, and ImageGen only for new or substantially redesigned visuals.
- GitHub Issues as durable research and plan artifacts.
- Adversarial Plan audits with stable findings and authenticated remediation lineage.
- Deterministic task parsing and an authorization-bound native GitHub issue graph for complex Plans.
- Structured Plan and Implement exit contracts.
- External-action verification: a staged upload or optimistic UI is never treated as complete.
- Independent execution audits before phase transitions.
- Phase retrospectives with a fixed, evidence-backed rubric and degradation detection.
- Autonomous phase transitions only after an independent technical-confidence judgment passes.

## Proportionate delivery

The workflow selects the smallest tier that can responsibly complete the request. A user may ask
for a higher tier; hard-risk signals always raise the floor.

| Tier | Use it for | Required harness |
| --- | --- | --- |
| Quick | Local, clear, reversible work | Current source evidence and a targeted check. |
| Balanced | Moderate uncertainty or multi-surface work | A concise contract, two current sources, checks, and verified external actions. |
| Deep | High-risk, ambiguous, cross-team, production, privacy, or protected-write work | The complete Research → Plan → Implement → Review artifact chain. |

The agent runs the deterministic router internally. It should not ask users to select a tier or
provide a routing file. Direct user intent opens a progress corridor for ordinary scoped
inspection, edits, tests, reconciliation, dependent recovery, branches, commits, review-branch or
pull-request publication, scoped issues, and verified read-back. The agent pauses only for named
hard boundaries: protected external writes, destructive or irreversible work, production/release
changes, sensitive-data access, missing authority, or material architecture ambiguity. Unknown or
contradictory risk classifications fail closed.

All tiers share the digest-chained
[`context-capsule/v1`](references/context-capsule-v1.md) persistence contract. Assurance remains a
separate choice: Light buys local integrity and the bounded progress corridor; Heavy preserves every
Deep gate. Existing phase/freeform invocations and legacy Deep remain Heavy. Legacy Quick/Balanced
map to Light with their prior subprofile gates intact. The immutable prepared authority, separate
final receipt, and `assurance-policy/v1` rules are defined in
[`projection-bundle/v1`](references/projection-bundle-v1.md).

```text
$evidence-gated-delivery [--assurance light|heavy] <research|plan|implement|review|orchestrate> <authority-or-goal>
```

The selector occurs at most once immediately after the skill name; `status` rejects it. Light never
authorizes merge, deployment/release, production/cloud changes, provider activation,
credential/account mutation, sensitive-data access, deletion, force-overwrite, or telemetry writes.

A Deep run uses one coherent workflow release. Before mutation, the repository release can be
compared with the active installed copy using `verify_skill_sync.py`; detected drift must never be
papered over by mixing scripts from both versions. The current repository release remains the
authority for repository work until its reviewed distribution update is installed.

```bash
python3 scripts/intent_router.py --scope medium --ambiguity medium \
  --novelty medium --external-impact ordinary
```

Quick and Balanced may use `scripts/validate_tier.py` when a durable receipt is useful; they do
not require it to proceed. Deep continues to use `scripts/validate_run.py` unchanged.

## Continuous improvement

Every completed phase has an independent retrospective. The rubric uses seven fixed dimensions:

| Dimension | Weight |
| --- | ---: |
| Evidence integrity | 20 |
| External-action verification | 20 |
| Workstream identity | 15 |
| Phase-contract compliance | 15 |
| Semantic and privacy safety | 15 |
| Delivery reliability | 10 |
| Learning quality | 5 |

The next phase is blocked below 85/100, when either evidence or external verification is below
3/4, or when the score drops five points from the prior baseline. Remediation must be recorded and
rechecked; the threshold is never lowered to hide degradation.

## Autonomous transitions and stop gates

The default policy is autonomous: Research → Plan → Implement proceeds without a human approval
prompt only after the predecessor has a `VALID` receipt, execution audit, retrospective, and a
fresh Phase Transition Judge. That judge must independently find the phase technically accurate
(`3/4` or higher), return an integer confidence of `8/10` or higher, identify no unresolved high or
critical finding, and bind its judgment to the predecessor receipt hash. The validator—not the
judge—derives the final `auto_proceed` decision.

Users can request a human stop before `plan`, `implement`, or `review`. A stop gate always wins,
even over a 10/10 judgment, and requires a later explicit user release recorded in the run manifest.
Autonomy never authorizes protected external writes, destructive or irreversible actions,
production/release changes, sensitive-data access, work with missing authority, or material
architecture ambiguity affecting public contracts, persistence, security, or irreversible
migrations.

## Layout

```text
SKILL.md                 Workflow instructions for an agent harness
references/              Phase contracts, role contracts, publication, retrospective rubric
scripts/init_run.py      Creates a durable run manifest
scripts/intent_router.py Deterministically grades the delivery tier and authority envelope
scripts/delegation_router.py Selects a solo, one-off, parallel, or phase-isolated task topology
scripts/progress_control.py Detects stalls and reports the execution frontier, gate costs, and outcome signals
scripts/validate_tier.py Validates Quick and Balanced receipts; delegates Deep to the existing validator
scripts/validate_run.py  Validates phase transitions and writes receipts
scripts/preflight_plan.py Validates a draft plan before publication
scripts/plan_protocol.py Plan protocol, task, audit, event, graph, and recovery invariants
scripts/plan_lint.py       Deterministic Plan-structure lint
scripts/migrate_plan_protocol.py Explicit legacy-to-v2 manifest migration
scripts/visual_applicability.py Deterministic visual-evidence mode selection
scripts/record_retrospective.py Records fixed-rubric phase retrospectives
scripts/verify_skill_sync.py Verifies an installed skill matches this release
scripts/record_transition_outcome.py Records outcomes for confidence calibration
scripts/transition_calibration.py Reports whether confidence predicts outcomes
scripts/run_status.py       Emits a machine-readable run dashboard
bundled-skills/feature-to-spec/ Portable Plan-phase EARS specification skill
bundled-skills/plan-auditor/ Portable adversarial Plan review contract
bundled-skills/plan-to-graph/ Protected GitHub issue-graph transaction contract
tests/                   Standard-library harness tests
```

## Operational feedback

Before relying on a locally installed copy, compare it with a release checkout:

```bash
python3 scripts/verify_skill_sync.py \
  --installed-skill ~/.agents/skills/evidence-gated-delivery
```

After a transition, record downstream CI, review, and defect outcomes, then periodically inspect
whether the `8/10` threshold is calibrated:

```bash
python3 scripts/record_transition_outcome.py --input outcome.json
python3 scripts/transition_calibration.py \
  ~/.codex/evidence-gated-delivery-history/transition-outcomes.jsonl
```

At any point, render the run dashboard from its manifest:

```bash
python3 scripts/run_status.py /tmp/evidence-gated-delivery-run.json
```

## Requirements

- Python 3. The scripts use only the Python standard library.
- A local Git worktree for `init_run.py`.
- An authenticated `gh` CLI when a phase needs to create or read GitHub artifacts, or resolve a
  private GitHub attachment.
- An agent runtime with subagents. ImageGen is required only when the phase-bound disposition is
  `generative_mockup`; runtime-capture and nonvisual Plans do not generate images.

## Hi-fi planning gates

New runs use immutable `plan-protocol/v2`. A Plan is linted, independently audited, bound to the
exact remotely read GitHub issue body, and parsed into stable `T-NNN` tasks. `graph-policy/v1`
selects `NO_GRAPH` only for at most three tasks, no dependency edges, and one owner lane; every
other Plan publishes a deterministic native GitHub sub-issue graph.

Graph publication is implied by a validated Plan. The frozen draft, authenticated account,
repository, parent, CLI capabilities, child bodies, and dependency edges must all match current
state before every write. Recovery resumes only an exact Plan-bound subset and never edits or
deletes conflicting remote state.

The bundled planning skills are portable reimplementations informed by
`lousy-agents/skills` at the pinned provenance revision. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
[references/source-provenance.json](references/source-provenance.json).

## Initialize a run

From a clone of this repository, create a manifest before substantive phase work:

```bash
python3 scripts/init_run.py \
  --mode research \
  --goal "Your goal" \
  --repo /path/to/git-worktree \
  --output /tmp/evidence-gated-delivery-run.json
```

Initialization creates a receipt; it does **not** complete Research. Follow [SKILL.md](SKILL.md)
and its linked phase contracts to collect evidence, publish and read back the required artifacts,
record the independent audit, then validate the phase:

```bash
python3 scripts/validate_run.py /tmp/evidence-gated-delivery-run.json --phase research
```

A nonzero validation result is a stop gate, not a warning. Run the package tests separately:

```bash
python3 -m unittest discover -s tests -v
```

## Install as an agent skill

The repository is compatible with the open `skills` CLI. Install it directly for Codex:

```bash
npx skills@latest add mknoth197/evidence-gated-delivery \
  --skill evidence-gated-delivery --agent codex --yes
```

To install both the workflow and its bundled `feature-to-spec` dependency, include `--full-depth`:

```bash
npx skills@latest add mknoth197/evidence-gated-delivery \
  --full-depth --skill evidence-gated-delivery feature-to-spec --agent codex --yes
```

Or let the CLI choose the destination agent interactively:

```bash
npx skills@latest add mknoth197/evidence-gated-delivery
```

The CLI defaults to project scope. To install globally, add `-g`; for example:

```bash
npx skills@latest add mknoth197/evidence-gated-delivery \
  --skill evidence-gated-delivery --agent codex --global --yes
```

The CLI supports other compatible agents as well. See the
[skills CLI documentation](https://www.skills.sh/docs/cli) for current options.

## Provider evidence adapters

Codex Desktop remains the built-in adapter: it verifies the persisted spawn,
callback, and UUID-backed session lineage. Other runtimes must not imitate that
layout or weaken the gate. Instead, an integration may submit a
`provider_delegated` audit receipt backed by a normalized JSON file. The
validator requires all of the following before it accepts the receipt:

- provider name and version;
- exact parent and immediate-child session IDs plus delegated role;
- timezone-aware start and completion timestamps;
- an existing transcript under an explicitly allowlisted local root, with its
  SHA-256;
- exact final-result bytes and SHA-256; and
- a SHA-256 binding all of the preceding parent/child evidence.

The first supported external shape is `claude_code`: a project hook can write
this JSON receipt from its session and subagent-stop data. Hook output alone is
not enough—the validator fails closed when its transcript path, digest, role,
parent/child binding, or result hash is missing or mismatched. Configure the
runtime in `provider_context` and name the receipt path in the audit entry:

```json
{
  "provider_context": {
    "provider": "claude_code",
    "provider_version": "<observed Claude Code version>",
    "allowed_transcript_roots": ["/absolute/project-controlled/receipt-root"]
  },
  "trace_audits": [{
    "receipt_kind": "provider_delegated",
    "agent_id": "provider-child-session-id",
    "role_marker": "Execution auditor phase: research",
    "result": "exact auditor result",
    "result_sha256": "sha256 of exact auditor result",
    "verified_event_ids": ["E1"],
    "provider_receipt_path": "/absolute/project-controlled/receipt-root/audit.json"
  }]
}
```

This is a capability contract, not a claim that every Claude Code deployment
provides equivalent evidence. If the integration cannot make the receipt and
its transcript safely readable, the run remains blocked.

## Safety model

The workflow distinguishes ordinary, recoverable writes from protected mutations. Direct intent
authorizes ordinary scoped work such as review branches, pull requests, issues, comments, and
deterministic graph publication; each is read back before it is reported as verified. It stops for
merge, deploy or release, production flags or cloud resources, credential or account changes,
sensitive-data access, deletion, force-overwrite, missing authority, or material architecture
ambiguity. Validators remain mandatory evidence gates, but they do not create additional approval
gates for an in-scope repair.

## License

MIT. See [LICENSE](LICENSE).
