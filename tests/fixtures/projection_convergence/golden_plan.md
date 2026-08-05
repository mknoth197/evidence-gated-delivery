# Projection convergence Plan

`D-001` `UD-001` `AC-001` through `AC-001` `T-001` through `T-001`
`M-001` through `M-001`

## Problem Statement
Implement a backend validator workflow that proves every derived validation record uses one authority buffer.

## Personas
Repository maintainers who need deterministic validation evidence.

## Value Assessment
One immutable authority removes split-brain validator claims.

## User Stories
As a maintainer, I want converged validator records so that a mutation cannot pass one gate and fail another silently.

## Design
```mermaid
flowchart LR
  A[Authority bytes] --> K[Projection kernel]
  K --> P[Typed validator records]
```

## Acceptance Criteria
- WHEN the kernel prepares validation evidence, THE VALIDATOR SHALL bind every validator record to the same input digest. <!-- AC-001 -->

## Tasks
- [ ] **T-001 — Converge validator records.** Objective: bind task, graph, visual-policy, audit, and preflight validator records to one authority buffer. Context: independent rereads can observe different issue revisions. Affected modules: `scripts/plan_tasks.py`. Requirements: use closed adapter versions and input digests. Verification: run golden and adversarial validator fixtures. Complete when: every derived payload proves the same authority digest. Owner lane: plan-conformance. `depends_on: []`.

## Out of Scope
Provider execution and protected GitHub writes.

## Mockup Accounting Matrix
| Visual requirement | Acceptance criterion | Task | Verification |
| --- | --- | --- | --- |
| Nonvisual validator workflow | AC-001 | T-001 | Projection convergence tests |

## Cross-Reference
Research: https://github.com/mknoth197/evidence-gated-delivery/issues/22
