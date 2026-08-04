# Context Capsule v1

`context-capsule/v1` is the mandatory, compact persistence substrate for every assurance tier. It
preserves semantic continuation state, not a transcript and not proof that Heavy assurance passed.
Capsules are immutable generations linked by digest; checkpoint writers use compare-and-swap (CAS).

## Envelope

The canonical capsule is a JSON object with exactly these top-level fields:

| Field | Type | Contract |
| --- | --- | --- |
| `schema_version` | string | Exactly `context-capsule/v1`. |
| `capsule_id` | string | Stable opaque ID for the capsule lineage. |
| `generation` | integer | Starts at `1`; increments by exactly one within a lineage. |
| `previous_digest` | string or null | Lowercase SHA-256 of the previous canonical capsule; null only at generation 1 or an explicit fork. |
| `parent_capsule` | object or null | On fork, `{capsule_id, generation, digest}` of the verified parent; otherwise null. |
| `objective` | string | Bounded current objective. |
| `settled_decisions` | array | Bounded decisions with `id`, `decision`, `evidence_refs`, and `settled_at`. |
| `source_revisions` | array | Authority locators with `source_id`, `revision`, and `digest`; no mutable source is accepted without a revision or digest. |
| `evidence_refs` | array | Bounded references with `id`, `kind`, `locator`, `digest`, and `access`; never copied artifact bodies. |
| `execution_frontier` | object | `state`, `next_action`, `responsible_component`, and optional `blocker_ref`. |
| `unresolved_questions` | array | Bounded questions with owner and next evidence action. |
| `next_action` | object | One safe action with `description`, `risk_classification`, and `authority_ref`. |
| `assurance` | object | `requested`, `effective`, `achieved`, `selection_origin`, and optional `legacy_subprofile`. |
| `bundle_ref` | object or null | Prepared bundle `bundle_id` and `prepared_digest` when one exists. |
| `privacy` | object | `classification`, `redactions`, `omitted_fields`, and `retention_hint`; redactions preserve provenance. |
| `created_at` | string | Timezone-aware creation timestamp. |
| `checkpointed_at` | string | Timezone-aware immutable-generation timestamp. |
| `digest` | string | Lowercase SHA-256 over canonical JSON excluding `digest`. |

The seven invariant semantic fields are `objective`, `settled_decisions`, `source_revisions`,
`evidence_refs`, `execution_frontier`, `unresolved_questions`, and `next_action`. Compaction may
reduce bounded detail but must preserve the meaning and provenance of every unsettled or active
item. `assurance.achieved` is an observed state; a useful capsule may truthfully say `light` or
`blocked` even when requested Heavy validation is pending or invalid.

Canonical JSON is UTF-8, object keys sorted lexicographically, no insignificant whitespace, and no
NaN or Infinity. Digest comparison is byte comparison after this canonicalization.

## Lifecycle and CAS

The closed lifecycle operation set is `create`, `checkpoint`, `resume`, `fork`, `compact`,
`supersede`, and `archive`.

- `create` writes generation 1 atomically and returns its digest.
- `checkpoint` accepts `expected_generation` and `expected_digest`; it writes generation + 1 only
  when both still match. A lost race writes no replacement capsule and emits
  `BLOCKED_CAPSULE_CONFLICT`.
- `resume` verifies every available digest link, the current source revisions, schema support, and
  the next action's authority before execution.
- `fork` verifies its parent and starts a new lineage at generation 1 with `previous_digest: null`
  and a non-null `parent_capsule`.
- `compact` checkpoints a semantically equivalent bounded form. It does not drop active blockers,
  evidence needed by settled decisions, or the source revision that anchors the frontier.
- `supersede` checkpoints a pointer to the successor lineage without rewriting history.
- `archive` is a final checkpoint state; resumption requires an explicit fork.

Writes use same-directory temporary creation, file flush, and atomic replace. A failure before the
replace leaves the prior verified generation authoritative. A failure after the replace is
resolved by rereading and verifying the new bytes; success is never inferred from an attempted
write.

## Validation and blocked states

Capsule validation fails closed and checkpoints the last verified frontier when possible:

| Blocker | Condition | Next safe action |
| --- | --- | --- |
| `BLOCKED_CAPSULE_INVALID` | Required field, type, canonical digest, or chain link is invalid. | Restore a verified generation or stop. |
| `BLOCKED_CAPSULE_CONFLICT` | CAS generation or digest differs. | Re-read, reconcile semantic changes, and checkpoint against the new head. |
| `BLOCKED_SOURCE_DRIFT` | A bound source revision or digest changed. | Reconcile against current authority before continuing. |
| `BLOCKED_UNSUPPORTED_SCHEMA` | No exact reader exists for the recorded version. | Use a compatible read-only reader or an explicit migration. |
| `BLOCKED_ASSURANCE_SELECTION` | Assurance selection is missing, duplicated, unknown, or invalid for the mode. | Correct the invocation; do not infer a downgrade. |
| `BLOCKED_REQUIRED_ESCALATION` | A named hard boundary prevents Light continuation. | Authorize the protected action, narrow scope, or stop. |
| `BLOCKED_MISSING_AUTHORITY` | The next action lacks matching authority. | Acquire exact authority or stop. |

Every blocker record contains `blocker_version`, `code`, `evidence_refs`, `responsible_component`,
`execution_frontier`, `next_safe_action`, and `recorded_at`. Unknown blocker codes do not become
success; readers report `BLOCKED_UNSUPPORTED_SCHEMA`.

## Privacy boundary

Capsules contain compact semantic state and content-addressed references. They must not contain
transcripts, hidden reasoning, credentials, tokens, secrets, raw sensitive payloads, private prompt
text, or duplicate issue/artifact bodies. A sensitive source uses a digest plus an access-controlled
locator. Redaction records the source field, reason, and replacement digest so provenance survives.

## Migration and rollback

Historical context may be wrapped in `legacy-envelope/v1` with `original_digest`,
`original_format`, `reader_version`, and `transactional_completeness: unproven`. Migration creates a
new capsule lineage and a receipt that binds the original hash; it never rewrites or relabels the
legacy artifact as a verified digest chain. Rollback selects the prior reader and preserves every
new capsule and receipt.

## Example

```json
{
  "schema_version": "context-capsule/v1",
  "capsule_id": "capsule-24",
  "generation": 2,
  "previous_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "parent_capsule": null,
  "objective": "Freeze T-001 workflow contracts",
  "settled_decisions": [{"id":"D-001","decision":"Persistence is mandatory and assurance is proportional","evidence_refs":["E-001"],"settled_at":"2026-08-03T16:30:00Z"}],
  "source_revisions": [{"source_id":"plan-24","revision":"cac311f36c83587a62327cb51a864f14b5fec15c80242acdfb7b9514deb74435","digest":"cac311f36c83587a62327cb51a864f14b5fec15c80242acdfb7b9514deb74435"}],
  "evidence_refs": [{"id":"E-001","kind":"github_issue","locator":"https://github.com/mknoth197/evidence-gated-delivery/issues/24","digest":"cac311f36c83587a62327cb51a864f14b5fec15c80242acdfb7b9514deb74435","access":"public"}],
  "execution_frontier": {"state":"ready","next_action":"Run contract tests","responsible_component":"workflow-contracts","blocker_ref":null},
  "unresolved_questions": [],
  "next_action": {"description":"Run contract tests","risk_classification":"ordinary_scoped_recoverable","authority_ref":"plan-24"},
  "assurance": {"requested":"heavy","effective":"heavy","achieved":"pending","selection_origin":"legacy_phase_command","legacy_subprofile":null},
  "bundle_ref": null,
  "privacy": {"classification":"public_metadata","redactions":[],"omitted_fields":["transcript","hidden_reasoning","credentials"],"retention_hint":"follow repository policy"},
  "created_at": "2026-08-03T16:30:00Z",
  "checkpointed_at": "2026-08-03T16:35:00Z",
  "digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
}
```
