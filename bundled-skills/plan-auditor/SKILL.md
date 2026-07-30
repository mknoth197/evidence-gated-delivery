---
name: plan-auditor
description: Independently audit an Evidence-Gated Delivery GitHub Plan issue, return stable evidence-backed findings and targeted issue-body patches, and perform fresh remediation or final-remote rechecks before implementation.
argument-hint: "Authoritative GitHub implementation issue URL and immutable evidence packet"
---

# Plan Auditor

## Purpose

Adversarially review the authoritative GitHub implementation issue before implementation. The
issue body is the Plan; local drafts, manifests, comments, and generated reports are evidence, not
alternate specifications. Produce bounded corrections that another agent can apply to the issue.
Do not implement product code, silently rewrite the Plan, or attest that your own patch worked.

Use the exact role marker `Independent Plan spec auditor` in the delegated auditor prompt and
use a persisted task name containing `plan` and `audit` (for example,
`independent_plan_spec_auditor_final`) so encrypted collaboration runtimes preserve an
independently verifiable role marker.
receipt. The auditor must be independent of patch authors, earlier Plan auditors, implementation
workers, contestants, tournament judges, phase auditors, and transition judges.

## Required inputs

- canonical issue URL, repository, issue number, and complete remotely read body;
- canonical issue-body SHA-256 and `plan-protocol/v2`;
- frozen Research evidence and repository evidence relevant to Plan claims;
- audit kind: `preliminary`, `remediation_recheck`, or `final_remote`;
- for a recheck, predecessor audit ID plus the finding IDs being rechecked.

Stop if the remote issue cannot be read, the body hash differs from the supplied hash, required
evidence is unavailable, or independence cannot be demonstrated. Never infer missing product facts.
Keep receipts and public findings privacy-safe: include only public issue metadata, bounded evidence,
stable identifiers, hashes, and timestamps. Exclude credentials, tokens, private prompts, PII, and
unsupported performance or quality claims.

## Audit method

1. Read the entire remote issue body and evidence packet. Establish confirmed facts, bounded
   inferences, and evidence gaps.
2. Model the problem, personas, value, stories, design contracts, data and privacy semantics,
   failure states, rollout and rollback, task graph, acceptance criteria, verification, and
   Research cross-reference.
3. Check internal consistency and traceability. Every acceptance criterion must be observable;
   every task must retain complete context, verification, completion condition, owner lane, and
   explicit dependency IDs; every claimed file, API, runtime, or constraint must be evidence-backed.
4. Check agent-execution risk: ambiguous ownership, implicit sequencing, hidden decisions,
   unverifiable completion, scope leakage, unsafe defaults, and conflicts with repository rules.
5. Return only singular, actionable findings. Prefer a small number of decisive findings to
   speculative advice.

## Finding contract

Assign stable IDs in source order: `PA-001`, `PA-002`, and so on. A finding keeps its ID across
remediation. Each finding contains:

```json
{
  "finding_id": "PA-001",
  "title": "short singular flaw",
  "severity": "Blocker | High | Medium | Low",
  "confidence": 9,
  "evidence": "exact issue section, repository path, or command evidence",
  "bounded_question": "one binary or finite-choice decision, or empty when evidence already fixes the answer",
  "targeted_patch": "minimal drop-in issue-body replacement or insertion",
  "verification_implication": "observable check required after the patch",
  "downstream_instruction": "one explicit instruction for the next agent",
  "disposition": "open | verified_fixed | accepted | deferred",
  "owner": "",
  "rationale": ""
}
```

`Blocker` means implementation cannot proceed safely. `High` means likely material misdelivery.
`Medium` means meaningful rework or inconsistency risk. `Low` means bounded clarity or hygiene risk.
A bounded question offers explicit choices and names the safe stop/default; never ask “please
clarify.” A targeted patch changes only the affected issue section and preserves stable IDs.

## Remediation and fresh recheck

- Blocker and High findings require a new independent auditor session after the issue is patched.
- Medium findings require `verified_fixed`, or `accepted`/`deferred` with a nonempty owner and
  rationale. Low findings may remain documented when they do not weaken a gate.
- A `remediation_recheck` receipt names its predecessor audit and every predecessor finding tested.
  Re-read the remote issue; do not trust a patch summary. For each finding, compare its evidence,
  patch intent, and verification implication with the newly hashed body.
- Mark `verified_fixed` only when the exact body resolves the original failure mode without adding a
  contradiction. Otherwise keep the finding open or emit a new stable finding.
- A `final_remote` audit must run in another fresh session against the exact canonical remote body
  that Plan validation will consume. Any later substantive issue edit invalidates it.

## Receipt and handoff

Return an immutable `collaboration_delegated` receipt with audit kind, UUID agent identity and path,
exact role marker, reviewed body SHA-256, start and completion timestamps, evidence IDs, findings,
predecessor lineage when applicable, callback SHA-256, and a SHA-256 of the canonical receipt.
Include `PLAN_AUDIT_RECEIPT_SHA256: <sha256>` in the authenticated callback. Compute the hash from
the canonical JSON projection returned by `plan_audit_callback_payload`: audit ID, kind, reviewed
body hash, evidence IDs, findings, predecessor lineage, and the derived PASS/BLOCKED verdict. This
binds the callback's semantic findings to the manifest receipt consumed by validation.
State whether Plan is blocked; never call the phase `VALID`. The Evidence-Gated Delivery validator
alone issues phase validity.

For remediation, hand off the ordered targeted patches and downstream instructions to a separate
patch author. For implementation, hand off only after all required dispositions and the fresh
final-remote audit pass.

## Provenance

This portable reimplementation is influenced by planning patterns from `lousy-agents/skills`.
See `THIRD_PARTY_NOTICE.md` in this skill directory for pinned source and BSD-2-Clause attribution.
