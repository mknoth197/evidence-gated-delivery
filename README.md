# Evidence-Gated Delivery

An auditable Research → Plan → Implement → Review workflow for agentic engineering work.

It treats agents as **model + harness**: the model generates and reasons, while durable artifacts,
tests, independent review, validator receipts, and explicit stop gates make outcomes trustworthy.

## What it enforces

- Evidence-backed research before architecture selection.
- A three-concept design tournament with two independent judges.
- ImageGen-backed normative visuals, grounded in the current product shell.
- GitHub Issues as durable research and plan artifacts.
- Structured Plan and Implement exit contracts.
- External-action verification: a staged upload or optimistic UI is never treated as complete.
- Independent execution audits before phase transitions.
- Phase retrospectives with a fixed, evidence-backed rubric and degradation detection.

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

## Layout

```text
SKILL.md                 Workflow instructions for an agent harness
references/              Phase contracts, role contracts, publication, retrospective rubric
scripts/init_run.py      Creates a durable run manifest
scripts/validate_run.py  Validates phase transitions and writes receipts
scripts/preflight_plan.py Validates a draft plan before publication
scripts/record_retrospective.py Records fixed-rubric phase retrospectives
tests/                   Standard-library harness tests
```

## Requirements

- Python 3. The scripts use only the Python standard library.
- A local Git worktree for `init_run.py`.
- An authenticated `gh` CLI when a phase needs to create or read GitHub artifacts, or resolve a
  private GitHub attachment.
- An agent runtime with subagents and ImageGen for the full Plan tournament. The validator can run
  anywhere with Python 3, but a Plan cannot pass without the required evidence from those tools.

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

## Safety model

The workflow defaults external systems to read-only. It requires explicit authorization for
publication, deployments, feature flags, and other writes. It records the mutation, read-back,
and durable output before reporting an action as verified.

## License

MIT. See [LICENSE](LICENSE).
