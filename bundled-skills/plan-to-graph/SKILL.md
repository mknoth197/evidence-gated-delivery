---
name: plan-to-graph
description: Convert an approved Evidence-Gated Delivery GitHub Plan issue into an authorized, remotely verified native GitHub child-issue dependency graph with fail-closed recovery.
argument-hint: "Authoritative GitHub implementation issue URL and current graph-policy receipt"
---

# Plan to Graph

## Purpose and boundary

Translate the `## Tasks` section of one authoritative GitHub implementation issue into one level of
native child issues plus native blocked-by edges. The parent issue remains the complete Plan. Do not
modify source code or derive tasks from prose. Do not use a local spec as authority, emulate
relationships with comments or checklists, or mutate GitHub before the exact draft is explicitly
authorized.

Run only when the validator recomputes `GRAPH_REQUIRED` under `graph-policy/v1`. For `NO_GRAPH`,
record the policy receipt and stop without GitHub graph writes.

## Read-only preflight

Before drafting, record:

1. `gh version` and `gh auth status`;
2. authenticated login and immutable account ID from GitHub read-back;
3. exact repository identity and parent issue URL/number;
4. successful remote read of the complete parent title, body, state, URL, children, parent,
   `blockedBy`, and `blocking`;
5. native capability evidence for child creation, blocked-by mutation, and all relationship
   read-back fields.

Missing authentication, account ID, repository access, native relationship capability, full body,
or read-back support is a blocker. Stop; do not substitute another tracker or degraded relationship.
Recheck identity, repository, parent, capability, and authorization immediately before every write.

## Parse and freeze

Parse only direct tasks under `## Tasks` using the `plan-protocol/v2` grammar. IDs must be unique and
sequential. Dependencies come only from `depends_on`, must name existing task IDs, and must be
acyclic.

Create one deterministic child per task:

- exact task title;
- stable marker `<!-- evidence-gated-delivery-task:T-NNN -->`;
- complete task Markdown, unchanged, including objective, context, affected modules, requirements,
  verification, completion condition, owner lane, and dependency declaration;
- canonical child-body SHA-256.

The frozen draft contains the parent URL, repository, ordered children with titles, full bodies,
markers and hashes, and ordered `blocked child <- blocker` edges. Hash the complete canonical JSON
object. Once presented, any title, body, marker, hash, edge, parent, repository, capability, or
identity change creates a new draft.

## Collision scan and reconciliation

Read all current children and relationship state before authorization and again before resuming.
Compare stable markers first, then exact titles, full canonical body hashes, parent membership, and
edges. Classify the remote state:

- `EXACT_MATCH`: every authorized item and edge matches. Reuse it; make no duplicate write.
- `AUTHORIZED_MISSING`: observed state is an exact subset of the same authorized draft. After
  revalidating authorization, create or wire only the missing authorized items.
- `CONFLICT`: any unknown marker, duplicate, changed title/body, wrong parent, extra or changed
  edge, or non-subset state. Stop `BLOCKED`. Do not edit, close, delete, duplicate, or adopt it.
  Freeze a replacement draft and obtain new explicit authorization.

Loose search results are collision candidates, never proof. Include open and closed issues. Do not
repair a conflict by guessing intent.

## Explicit authorization gate

Present the exact frozen draft before any mutation: authenticated login and account ID, repository,
parent, capability receipt, each child title and full-body hash, ordered edges, collision result,
and draft SHA-256. Ask the user to explicitly authorize that exact draft.

Authorization evidence is an authenticated parent-user-message receipt binding the parent thread,
exact draft SHA-256, exact message SHA-256, and message timestamp. The message itself must explicitly
approve or authorize that exact graph draft hash. Approval of a Plan, a previous draft, or
“continue” from before the draft was frozen is not graph authorization. Reauthenticate the receipt
before every write. Drift invalidates authorization. Never broaden authorization to later edits or
unknown recovery actions.

## Transaction and action ledger

Use the repository's `scripts/graph_transaction.py` coordinator (or an
equivalent adapter that preserves its guard/runner/read-back contract). The
coordinator revalidates identity, capability, authorization, and privacy before
every mutation; records `attempted` before dispatch; then requires authenticated
remote read-back before recording `verified`.

After authorization, perform one mutation at a time in draft order:

1. Re-run identity, repository, parent, capability, and collision preflight.
2. Create one missing child with its complete frozen body and native parent relationship.
3. Read it back and verify URL/number, title, marker, full body hash, and parent before continuing.
4. After all children verify, add one missing native blocked-by relationship per ordered edge.
5. Read both endpoints and verify `blockedBy`/`blocking` symmetry before continuing.

Append an `attempted` action record immediately before dispatching every mutation command, including
commands that later fail:

```json
{
  "action_id": "GA-001",
  "kind": "create_child | add_blocked_by",
  "target": "stable task ID or edge",
  "attempted_at": "ISO-8601",
  "status": "attempted | verified | blocked",
  "command_summary": "privacy-safe operation",
  "remote_url": "",
  "readback_evidence": [],
  "error": ""
}
```

Command success is only `attempted`; change it to `verified` only after authenticated remote read-back
agrees. On any failure or mismatch, record `blocked`, stop immediately, report verified
and unverified actions, and preserve the frozen bodies for recovery. Never repeat an unverified
write until reconciliation classifies current state.

Keep drafts, ledgers, and public evidence privacy-safe. Include only public issue metadata, stable
task identifiers, timestamps, hashes, bounded errors, and command summaries. Exclude credentials,
tokens, private prompts, PII, and unsupported performance or quality claims.

## Recovery and completion proof

On resume, read remote state first. `AUTHORIZED_MISSING` permits only the exact missing subset from
the still-current authorized draft; preserve prior action ordering and append new records.
`EXACT_MATCH` needs no mutation. `CONFLICT` needs a new draft and new authorization.

Completion requires authenticated remote proof of every stable marker, exact child body hash,
native parent, ordered edge, and ledger ordering. Report URLs and hashes, but do not call Plan
`VALID`; return graph evidence to the Evidence-Gated Delivery validator.

## Provenance

This portable reimplementation is influenced by planning patterns from `lousy-agents/skills`.
See `THIRD_PARTY_NOTICE.md` in this skill directory for pinned source and BSD-2-Clause attribution.
