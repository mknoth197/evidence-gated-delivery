# Continuous Improvement and Degradation Control

Every Evidence-Gated Delivery run includes an independent retrospective after each completed
phase. The retrospective begins in the background as soon as the phase's evidence packet is
frozen, but the next phase cannot validate until its predecessor's retrospective is complete.

The retrospective is an audit, not a conversation summary. It reads the manifest, receipts,
action ledger, durable artifacts, and relevant trace evidence. It must identify strengths,
failures, root causes, and concrete harness changes. It must not award points based on prose alone.

## Fixed Rubric

Score each dimension from 0 to 4 using observed evidence. The weighted score is calculated by
`record_retrospective.py`; reviewers do not choose the total.

| Dimension | Weight | 4 means |
| --- | ---: | --- |
| Evidence integrity | 20 | Claims are traceable to current executable, runtime, or remote evidence. |
| External-action verification | 20 | Every claimed mutation has a complete verified action-ledger entry. |
| Workstream identity | 15 | Artifact scope and links match one frozen initiative with no contamination. |
| Phase-contract compliance | 15 | Required artifacts, roles, receipts, and gates are present before transition. |
| Semantic and privacy safety | 15 | Claims, visuals, and model boundaries respect the evidence and data contract. |
| Delivery reliability | 10 | Failures were surfaced promptly, retried safely, and never reported as complete early. |
| Learning quality | 5 | A concrete, testable improvement was captured from each material failure. |

The total threshold is 85/100. Evidence integrity and external-action verification must each be
at least 3/4. A score below threshold blocks the next phase until remediation is recorded and
independently rechecked.

## Degradation

The recorder compares a phase's weighted score with the most recent score for the same phase and
workflow version. A drop of 5 or more points is degradation. Degradation blocks the next phase
until the manifest records a root cause, one or more remediation actions, and a recheck result.
Never lower the threshold to absorb degradation; fix the harness, evidence, or workflow instead.

## Required Entry

Each `phase_retrospectives` item contains phase, independent auditor identity, fixed-dimension
scorecard, evidence per dimension, calculated total, baseline/delta, findings, remediation, and
status. Use the recorder rather than hand-editing totals.

## Invocation

```text
python3 <skill-dir>/scripts/record_retrospective.py <manifest.json> \
  --phase research --input <auditor-result.json>
```

The next phase validator is the gate. A retrospective may run concurrently with non-mutating
preparation, but no plan, implementation, or review transition is valid until the required entry
is complete and any degradation is remediated.

## Gate economics

Every optional Heavy gate records a local-only `gate-economics/v1` entry with stable `gate_id`,
name, applicability predicate and result, distinct failure class, expected and actual latency,
cost proxy (`UNKNOWN` when unavailable), finding or no-finding, remediation, raw denominator,
duplicate-finding count and rate, and downstream outcome or `INSUFFICIENT_EVIDENCE`. Duplicate
failure classes are non-authoritative diagnostics. They may motivate a human-reviewed policy
proposal but never retire or weaken a gate automatically.

Required gates remain `active`. Retirement requires explicit `human_review` evidence with reviewer,
decision, and timestamp. Gate economics stays in local run artifacts: it contains no telemetry,
remote endpoint, ingestion configuration, or network-write instruction. Retrospectives carry only
the bounded count and diagnostic summary needed to compound workflow learning.
