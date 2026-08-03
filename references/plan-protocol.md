# Plan Protocol v2

`plan-protocol/v2` is the machine-checkable Plan contract for newly initialized
Evidence-Gated Delivery runs. GitHub implementation issues remain the only normative Plan
artifacts; manifests and checkpoint receipts are execution evidence.

The stable `plan_protocol.py` entrypoint is a compatibility facade. Focused modules own protocol
core and activation storage, canonical task parsing and linting, hash-chained events and migration,
audit receipts, and protected graph state. `validate_run.py` remains phase orchestration; trace,
GitHub graph I/O, Plan, Review, and cross-phase gates live behind injected adapters so pure
contract checks stay separable from environment reads.

## Versioning

- New runs record `plan_protocol_version: plan-protocol/v2`.
- A legacy manifest must explicitly retain `plan-protocol/v1`; a missing field fails closed.
- Supported values are `plan-protocol/v1` and `plan-protocol/v2`; every other value fails closed.
- A recorded version is immutable. Migration is an explicit command that switches
  `workflow_version` to `evidence-gated-delivery/plan-protocol-v2` and appends a
  `protocol_migrated` event preserving the previous event-chain head and prior workflow version.
  Initialization and migration also persist a derived, write-once, versioned activation receipt under the
  caller's Codex home, keyed by `run_id` and bound to the authenticated parent thread, activation
  event, run start, repository baseline, goal, mode, workflow, and protocol. Validation uses direct run lookup
  plus registry lookup anchored only on the authenticated parent thread, then compares every other
  binding. That external receipt is the
  validator-bound root outside the mutable manifest and event chain, so changing or removing the
  run ID, restoring legacy fields, and deleting events cannot make an activated run eligible for
  v1 validation. V2 validation requires the receipt, exact manifest/receipt identity fields, and
  the bound activation event in the hash chain. Migration `--dry-run` prepares and prints the
  candidate without writing either the manifest or activation registry. If an interruption leaves
  the write-once activation receipt before the manifest replacement, a retry reuses that receipt's
  exact event ID and timestamp after verifying all stable workflow-identity and legacy-manifest
  bindings; it never
  invents conflicting activation evidence. Current and transitional receipts bind goal and mode.
  Identity-unbound legacy receipts are explicitly quarantined and require an authenticated upgrade;
  they are never silently accepted, rewritten, or rebound.

## Canonical issue body

Canonicalization converts CRLF and bare CR line endings to LF. It does not trim whitespace,
remove comments, reorder sections, or ignore URLs, criteria, tasks, or relationships. The
canonical UTF-8 bytes are hashed with SHA-256.

## Stable task grammar

Every direct Markdown task under `## Tasks` has this shape:

```text
- [ ] **T-NNN — Title.** Objective: ... Context: ... Affected modules: ...
  Requirements: ... Verification: ... Complete when: ... Owner lane: lane.
  `depends_on: [T-NNN, ...]`.
```

Required fields are `Objective`, `Context`, `Affected modules`, `Requirements`, `Verification`,
`Complete when`, `Owner lane`, and `depends_on`. IDs are unique and sequential from `T-001`.
Dependencies name existing task IDs, never self-reference, and form an acyclic graph. Dependency
edges come only from `depends_on`; prose is not interpreted.

Each parsed task receipt contains:

```json
{
  "task_id": "T-001",
  "title": "Freeze contracts and schemas",
  "body": "complete task Markdown",
  "body_sha256": "sha256",
  "objective": "...",
  "context": "...",
  "affected_modules": ["..."],
  "requirements": "...",
  "verification": "...",
  "complete_when": "...",
  "owner_lane": "core",
  "depends_on": []
}
```

## Graph policy

`graph-policy/v1` is deterministic:

- `NO_GRAPH` when task count is at most three, dependency edge count is zero, and owner-lane count
  is exactly one.
- `GRAPH_REQUIRED` otherwise.

The receipt records the version, disposition, task count, edge count, owner lanes, task-set hash,
and evaluation timestamp. Agents do not select the result.
For `NO_GRAPH`, capability, draft, authorization, action, and remote-state evidence must all be
empty. When remote checks are enabled, the parent issue must also have no workflow-owned child
marker. Graph draft, authorization, action, or reconciliation events are also forbidden;
contradictory immutable history fails closed even if mutable manifest fields were cleared.

## Plan audit

`plan_audits` is an append-only array of authenticated receipts. Every receipt records:

- unique UUID `agent_id`, `agent_path`, and role marker
  `Independent Plan spec auditor`;
- `kind`: `preliminary`, `remediation_recheck`, or `final_remote`;
- exact `reviewed_body_sha256`, immutable result hash, start/completion timestamps, and evidence
  IDs;
- findings with stable ID, severity (`Blocker`, `High`, `Medium`, `Low`), confidence, evidence,
  bounded question when needed, targeted patch, verification implication, downstream instruction,
  and disposition;
- predecessor audit and finding IDs for a remediation recheck.

The authenticated callback includes `PLAN_AUDIT_RECEIPT_SHA256: <sha256>`, computed from the
canonical semantic projection of the audit ID, kind, reviewed body hash, evidence IDs, findings,
predecessor lineage, and derived PASS/BLOCKED verdict. Validation rejects a callback whose marker
does not match the exact manifest receipt, even when callback and receipt hashes are independently
well formed.

Blocker and High findings require a fresh independent `remediation_recheck` with `verified_fixed`
disposition and exact predecessor lineage. `verified_fixed` is invalid on preliminary or
`final_remote` receipts. Medium findings require the same recheck, or `accepted`/`deferred` with nonempty owner and
rationale. Final validation requires a fresh `final_remote` receipt for the exact canonical remote
issue-body hash. Auditor IDs cannot overlap patch authors, contestants, tournament judges, phase
auditors, transition judges, implementation workers, or predecessor Plan auditors.

## Hash-chained events

`plan_events` is append-only. Each event contains sequential `sequence`, stable `event_id`, event
`type`, ISO-8601 `recorded_at`, privacy-safe `payload`, `previous_event_sha256`, and
`event_sha256`. The event hash is SHA-256 of canonical compact JSON for every field except
`event_sha256`. The first event uses 64 zeroes as its previous hash.

Allowed event types are:

- `protocol_initialized`
- `protocol_migrated`
- `candidate_linted`
- `audit_completed`
- `finding_dispositioned`
- `issue_read_back`
- `graph_policy_evaluated`
- `graph_draft_frozen`
- `graph_action_recorded`
- `graph_reconciled`
- `checkpoint_issued`
- `phase_validated`

Checkpoint status is `CHECKPOINT_VALID`; it never substitutes for the phase validator's `VALID`
receipt.

## Protected graph transaction

A frozen graph draft is deterministically derived from the canonical tasks parsed from the
authoritative implementation issue. It records the parent issue URL, repository, exact child titles
and complete bodies, stable markers, child-body hashes, and canonical dependency edges. Its SHA-256
covers the complete canonical object. Validation requires the supplied draft to equal that derived
object before publication or reconciliation.

Publication is valid only when it binds:

- authenticated GitHub login and immutable account ID;
- exact repository and parent issue;
- a current capability receipt proving native parent, blocking, and read-back support;
- graph draft SHA-256, every child-body SHA-256, and every edge;

The validated Plan is the publication authority; no separately authorized user message is required.
Any identity, repository, parent, capability, or draft drift blocks publication before a write. The
transaction guard rechecks these preconditions immediately before every write. Graph action records
use `attempted`, `verified`, or `blocked`; a successful command is not `verified` until remote
read-back agrees.

## Reconciliation

Remote graph state has exactly three recovery classes:

- `EXACT_MATCH`: reuse the verified item.
- `AUTHORIZED_MISSING`: resume only missing items when all observed state is an exact subset of the
  same current Plan draft.
- `CONFLICT`: stop in `BLOCKED`; do not edit, delete, close, or duplicate unknown state. Freeze a
  new draft and re-evaluate publication preconditions.

GitHub relationship connections are a set-valued contract. The API may return the same blockers in
newest-first or another presentation order, so reconciliation compares exact edge membership while
still rejecting duplicates, missing edges at the final gate, and unexpected edges.

Final graph proof verifies stable task markers, child body hashes, parent membership, exact
dependency-edge membership, and action-ledger chronology from authenticated remote read-back.

## Privacy and provenance

Public artifacts and receipts contain only public issue metadata, stable task identifiers,
timestamps, hashes, and bounded findings. Never include credentials, tokens, private prompts, PII,
or unsupported performance or quality claims. Privacy inspection and evidence redaction cover
dictionary keys as well as values.

The bundled `plan-auditor` and `plan-to-graph` skills are portable reimplementations influenced by
the BSD-2-Clause `lousy-agents/skills` project. Their provenance record and required license notice
ship with the package.
