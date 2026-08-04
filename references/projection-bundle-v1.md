# Projection Bundle and Assurance Policy v1

This contract separates immutable preparation from accumulating execution evidence.
`projection-bundle/v1` freezes authority and deterministic projections. Later audits, provider
evidence, external actions, and read-backs are bound by `projection-transaction-receipt/v1`; they
never mutate the prepared identity.

## Immutable prepared bundle

A prepared bundle has these required fields:

| Field | Contract |
| --- | --- |
| `schema_version` | Exactly `projection-bundle/v1`. |
| `bundle_id` | Content-addressed stable identifier derived from `prepared_digest`. |
| `authority` | `kind`, `locator`, exact `bytes_digest`, `source_revision`, `byte_length`, and optional content-addressed sidecar. |
| `versions` | Exact `kernel`, `reader`, and `canonicalizer` versions. |
| `policy_versions` | Named version for every policy used, including `assurance-policy/v1`. |
| `assurance` | Requested/effective/selection-origin fields copied from the resolved policy. |
| `capsule_generation` | Capsule ID, generation, and digest used at preparation. |
| `slots` | Closed map of typed projection slots. |
| `parent_bundle` | Null or a prior `bundle_id` plus `prepared_digest`; never an implicit mutable pointer. |
| `prepared_at` | Timezone-aware preparation time. |
| `prepared_digest` | SHA-256 of canonical JSON excluding `prepared_digest` and derived `bundle_id`. |

The kernel reads authority bytes once into an immutable buffer. Every adapter receives those exact
bytes and the same authority digest. Re-reading a URL or file inside an adapter is a contract
violation. Changing source bytes, a reader, canonicalizer, kernel, policy version, assurance, slot
payload, or parent creates a different prepared digest.

Slots use exactly four states:

- `present`: `payload_digest`, `projection_version`, and typed payload or content-addressed locator
  are required.
- `omitted`: `policy_rule_id`, `reason`, and evidence of non-applicability are required.
- `pending`: `responsible_component` and `next_action` are required; pending cannot seal.
- `blocked`: `blocker_ref`, `responsible_component`, and `next_safe_action` are required.

Missing or unknown slot states fail as `BLOCKED_MISSING_REQUIRED_SLOT` or
`BLOCKED_UNSUPPORTED_SCHEMA`; absence is never interpreted as permission to omit. Each policy
declares the complete required slot set, so unknown additional slots also fail closed.

## Transaction receipt

`projection-transaction-receipt/v1` is a separate immutable final envelope with:

- `schema_version`, `transaction_id`, `bundle_id`, and exact `prepared_digest`;
- `intent` with risk classification, authority reference, and staged action digest;
- arrays of `audit_receipts`, `provider_receipts`, `graph_operations`, `gate_outcomes`, and
  `external_actions`;
- for every external action: target, started evidence, successful mutation receipt, remote read-back
  evidence, durable output, and state;
- `slot_outcomes` keyed to the prepared slot names without rewriting slot payloads;
- `final_state`, exactly `sealed` or `blocked`;
- `blockers`, `completed_at`, and `receipt_digest`.

`sealed` requires every policy-required slot to be present or policy-justified omitted, every
applicable validator to agree with the prepared digest and versions, and every started external
action to have matching verified read-back. A pending/blocked required slot, projection mismatch,
interruption before read-back, or unknown vocabulary produces a durable `blocked` receipt. The
receipt reports exact conflicting hashes/versions and never claims partial success.

External systems do not share an atomic commit with local storage. The transaction therefore stages
intent, performs the authorized mutation, and verifies remote state. `started` never means
`verified`; a command exit code or optimistic UI message is not read-back.

## Assurance policy

`assurance-policy/v1` resolves mode and assurance as separate axes. The explicit grammar is:

```text
$evidence-gated-delivery [--assurance light|heavy] <research|plan|implement|review|orchestrate> <authority-or-goal>
```

The selector, when present, occurs once immediately after the skill name. `status` rejects it
because status creates no run. Missing selector values, duplicate/unknown selectors, selectors in
later positions, unsupported modes, and extra positional assurance tokens block with
`BLOCKED_ASSURANCE_SELECTION`.

Resolved runs persist `mode`, `requested_assurance`, `requested_legacy_tier`,
`effective_assurance`, `legacy_subprofile`, `selection_origin`, and `achieved_assurance`.

| Input | Effective assurance | Legacy subprofile | Selection origin |
| --- | --- | --- | --- |
| Explicit `--assurance light` | `light` | null | `explicit_assurance` |
| Explicit `--assurance heavy` | `heavy` | null | `explicit_assurance` |
| Legacy phase command without selector | `heavy` | null | `legacy_phase_command` |
| Legacy inferred/freeform command | `heavy` | null | `legacy_inferred_command` |
| Legacy tier `quick` | `light` | `quick` | `legacy_tier` |
| Legacy tier `balanced` | `light` | `balanced` | `legacy_tier` |
| Legacy tier `deep` | `heavy` | `deep` | `legacy_tier` |

Legacy Quick and Balanced retain their existing subprofile gates and gain the mandatory capsule.
Legacy Deep maps exactly to Heavy and loses no manifest, audit, tournament, Plan Protocol v2,
visual, graph, provider-evidence, external-action verification, retrospective, or receipt gate. No
legacy invocation silently downgrades.

### Light progress corridor

Direct user intent permits only these ordinary scoped, recoverable action classes without a repeated
approval prompt: `inspect`, `edit`, `test`, `repair`, `branch`, `commit`, `publish_review_branch`,
`open_pull_request`, `update_scoped_issue`, `publish_deterministic_graph`, and
`verify_remote_readback`. An action must remain within the supplied scope and authority, be
recoverable, pass deterministic local integrity gates, record policy-justified omissions, and bind
remote read-back when it changes an external system.

Light never silently invokes a design tournament, multiple-auditor gate, phase receipt, remote
provider activation, or Heavy gate. Optional omissions remain explicit bundle slots; they are not
missing fields.

### Exactly six hard boundaries

The hard-boundary vocabulary is closed. Light stops only at these six IDs:

| Rule ID | Boundary |
| --- | --- |
| `protected_external_write` | A write whose target policy requires separate authority. |
| `destructive_or_irreversible` | Deletion, force-overwrite, or another action that cannot be safely recovered. |
| `production_or_release` | Deployment, release, merge-as-release, or production/cloud mutation. |
| `sensitive_data_access` | Access to credentials, secrets, protected personal data, or another sensitive source. |
| `missing_authority` | No exact current authority covers the action or target. |
| `material_architecture_ambiguity` | An unresolved choice changes a public contract, persistence format, security boundary, or irreversible migration. |

Unknown or contradictory classification fails closed; it does not create a seventh rule. A boundary
emits `BLOCKED_REQUIRED_ESCALATION` with `rule_id`, evidence, added Heavy controls, estimated
incremental latency/cost, and one of three explicit next actions: `authorize`, `narrow`, or `stop`.
Authorization covers only the named protected action; it does not convert the whole Light run to
Heavy or bypass other boundaries.

## Closed transaction blockers

Transaction blocker codes are `BLOCKED_PROJECTION_CONFLICT`, `BLOCKED_UNSUPPORTED_SCHEMA`,
`BLOCKED_MISSING_REQUIRED_SLOT`, `BLOCKED_REMOTE_READBACK_MISMATCH`,
`BLOCKED_REQUIRED_ESCALATION`, `BLOCKED_PROVIDER_EVIDENCE`,
`BLOCKED_INSTALLED_SKILL_DRIFT`, and `BLOCKED_MISSING_AUTHORITY`. Provider evidence includes
missing, stale, contradictory, or null required capability evidence. Unknown codes fail as
`BLOCKED_UNSUPPORTED_SCHEMA`.

## Privacy and external-action boundary

Bundles and receipts contain public metadata, stable IDs, timestamps, hashes, bounded findings,
policy decisions, and access-controlled locators. They never contain credentials, tokens, private
prompts, hidden reasoning, PII, sensitive payload bodies, or unsupported performance/quality
claims. Redaction examines keys as well as values and preserves a source digest.

Ordinary scoped publication in the progress corridor still requires a mutation receipt and remote
read-back. This contract does not authorize merge, deployment, release, production/cloud changes,
provider activation, credential/account mutation, sensitive-data access, deletion, force-overwrite,
or telemetry submission.

## Migration, distribution drift, and rollback

Migration shadow-derives a bundle beside legacy artifacts and compares semantics before cutover.
Historical artifacts use `legacy-envelope/v1` with `original_digest`, `original_format`,
`mapped_assurance`, and `transactional_completeness: unproven`; they are never relabelled `sealed`.
Every migration receipt binds the original and new hashes, reader versions, exact Deep-to-Heavy
mapping, comparison result, and rollback reader. Any unexplained divergence blocks cutover.

Repository-to-installed-skill comparison is read-only and emits exactly `IN_SYNC`, `DRIFT`, or
`UNKNOWN`, with both versions, hashes, and missing paths. It must not write, synchronize, or
overwrite a user-owned installed skill. Rollback restores legacy Deep readers without downgrading
Heavy, deleting capsules, or rewriting bundles/receipts. New schemas stay readable or explicitly
unsupported.

## Examples

Prepared bundle with an honest Light omission:

```json
{
  "schema_version": "projection-bundle/v1",
  "bundle_id": "pb1-7eb0ee486657",
  "authority": {"kind":"fixture","locator":"fixture:projection-bundle-v1-authority","bytes_digest":"4707cd5f9d18eb9c4704ee72ca03447942da46a42e151bbd561da0371d8c598a","source_revision":"4707cd5f9d18eb9c4704ee72ca03447942da46a42e151bbd561da0371d8c598a","byte_length":23,"sidecar":null},
  "versions": {"kernel":"projection-kernel/v1","reader":"github-issue-reader/v1","canonicalizer":"github-issue-body/v1"},
  "policy_versions": {"assurance":"assurance-policy/v1","visual":"visual-applicability/v1"},
  "assurance": {"requested":"light","effective":"light","selection_origin":"explicit_assurance","legacy_subprofile":null},
  "capsule_generation": {"capsule_id":"capsule-24","generation":2,"digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
  "slots": {
    "context": {"state":"present","projection_version":"context-capsule/v1","payload_digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","locator":"context-capsule:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
    "tournament": {"state":"omitted","policy_rule_id":"LIGHT_NO_TOURNAMENT","reason":"No design decision is requested","evidence_refs":["scope-24"]}
  },
  "parent_bundle": null,
  "prepared_at": "2026-08-03T16:40:00Z",
  "prepared_digest": "7eb0ee486657e126c9bc332c7d7cb78c3e2730b7cd7203b577bf849c3dfd7ff2"
}
```

Blocked transaction after an interrupted remote action:

```json
{
  "schema_version": "projection-transaction-receipt/v1",
  "transaction_id": "ptx1-876ac4e2bf06",
  "bundle_id": "pb1-7eb0ee486657",
  "prepared_digest": "7eb0ee486657e126c9bc332c7d7cb78c3e2730b7cd7203b577bf849c3dfd7ff2",
  "intent": {"risk_classification":"ordinary_scoped_recoverable","authority_ref":"plan-24","staged_action_digest":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"},
  "audit_receipts": [],
  "provider_receipts": [],
  "graph_operations": [],
  "gate_outcomes": [],
  "external_actions": [{"target":"issue-25","started_evidence":"event-1","mutation_receipt":"command-exit-0","readback_evidence":null,"durable_output":null,"state":"started"}],
  "slot_outcomes": {"context":{"state":"verified","prepared_state":"present"},"tournament":{"state":"omitted","prepared_state":"omitted"}},
  "final_state": "blocked",
  "blockers": [{"code":"BLOCKED_REMOTE_READBACK_MISMATCH","evidence_refs":["event-1"],"next_safe_action":"Read the remote issue and reconcile"}],
  "completed_at": "2026-08-03T16:41:00Z",
  "receipt_digest": "876ac4e2bf06dc6b82a67d354815d9dd1bcf9e2d39d0f8c565ac8b012ade2543"
}
```
