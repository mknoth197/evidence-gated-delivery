---
name: feature-to-spec
description: Convert validated research into an implementation-ready, EARS-format feature specification. In Evidence-Gated Delivery, use during Plan after the winning design is selected; write the complete result into the GitHub implementation issue rather than a repository spec file.
argument-hint: "Research issue URL or feature name"
---

# Feature to Spec

## Purpose

Turn a validated feature request into a testable implementation contract. This portable variant is
bundled with Evidence-Gated Delivery so Plan does not rely on an externally installed skill or a
particular repository's instruction files.

When used by Evidence-Gated Delivery, the GitHub implementation issue is authoritative. Do not
create `.github/specs/**`; redirect the output into the Plan issue.

## Inputs

- validated research artifact and frozen evidence packet;
- selected design, phase-bound visual disposition, applicable visual evidence, and
  disposition-accounting matrix;
- repository instructions, source conventions, tests, and deployment constraints; and
- current product/runtime evidence when the feature is user-facing.

## Procedure

1. Revalidate the research artifact against current source, runtime, data, and repository rules.
2. State the problem without prescribing an unsupported solution.
3. Identify personas and their primary and secondary value.
4. Write user stories and EARS acceptance criteria. Every criterion uses one of: Ubiquitous,
   Event-driven, State-driven, Optional, Unwanted, or Complex.
5. Freeze the Design: affected components, dependencies, contracts, data semantics, privacy,
   error/loading/empty states, feature/rollout gates, and rollback.
6. Include both a Mermaid data-flow diagram and sequence diagram where behavior crosses components
   or systems.
7. Break delivery into ordered tasks. Every task names objective, context, affected files or
   modules, requirements, verification, and completion condition.
8. Explicitly list out-of-scope and future considerations. Resolve material ambiguity or record it
   as a non-blocking open question with its owner.
9. Map each scope requirement and applicable runtime or normative-mockup requirement to acceptance
   criteria, tasks, and planned verification. Require a normative mockup only in
   `generative_mockup`.
10. Run an independent spec-quality review against the evidence packet. Correct factual, semantic,
    privacy, accessibility, or contract gaps before Plan validation.

## Required Implementation-Issue Sections

```text
## Problem Statement
## Personas
## Value Assessment
## User Stories
## Design
## Tasks
## Out of Scope
## Acceptance Criteria
## Mockup Accounting Matrix
## Cross-Reference
```

## Quality Rules

- Facts must trace to the frozen evidence packet; mark inferences and unresolved questions.
- Acceptance criteria are observable and testable, including non-happy, privacy, accessibility,
  responsive, availability, and rollback states where applicable.
- Tasks begin unchecked; implementation has not started in Plan.
- In `generative_mockup`, the final mockup URL and SHA-256 are present and every visual requirement
  has an accounting row. In `runtime_capture` or `none`, no image URL is invented and the
  disposition matrix covers the authoritative scope.
- The implementation issue links back to the research issue, and the research issue links forward.

## Handoff

Return the complete issue-ready body, then use Evidence-Gated Delivery publication and validation
rules. A Plan remains blocked until the issue, disposition-required evidence, action ledger,
independent audit, graph policy, and validator receipt all agree.
