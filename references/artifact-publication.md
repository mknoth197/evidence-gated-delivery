# Artifact Publication

## Purpose

Plan publication is a transaction. Issue creation alone does not complete Plan.

## Required Order

1. Draft the issue body locally using the exact top-level headings required by Plan Mode.
2. Keep `## Acceptance Criteria` top-level. Put all EARS statements in that section.
3. Keep implementation checkboxes in `## Tasks`.
4. Keep only normative visual rows in `## Mockup Accounting Matrix`.
5. Generate the final ImageGen file and record its SHA-256.
6. Obtain a durable HTTP(S) mockup URL using the routes below.
7. Put the exact URL and SHA-256 in the issue body.
8. Create or update the Plan issue.
9. Update the Research issue body, not only a comment, with the exact Plan issue URL.
10. Read both issue bodies back remotely.
11. Run the fresh execution auditor.
12. Run the Plan validator and retain its receipt.

Update the external-action ledger immediately after every upload, issue update, and read-back.
Commentary must use attempted, blocked, or verified for that action; only verified permits language
that the action is complete.

Do not write transition-evidence prose claiming validation before steps 10-12 finish.

## Publication Claim Gate

Uploading a file into a browser composer is `started`, not published. A durable mockup may be
called published only after all of the following are true:

1. the mutation request succeeded;
2. remote read-back shows the exact durable HTTPS image URL;
3. the implementation issue **body** (not only a comment) contains that URL and its SHA-256; and
4. the research and implementation issue bodies still contain reciprocal links.

Record the tool-event IDs for the mutation and read-back plus the durable URL and hash in the
execution-auditor packet. If the attachment comment posts without its image, or the issue body has
not yet been updated, publication remains blocked; state that exact condition and retry only the
missing transaction step.

## Durable Mockup Routes

Try in this order:

1. Use authenticated GitHub browser UI attachment upload when available. Retain the resulting
   `github.com/user-attachments/...` URL.
   For private repositories, pass the issue URL to Plan preflight with `--github-issue-url`; direct
   anonymous fetch may return 404, so preflight resolves the same durable attachment through
   authenticated rendered-issue read-back before comparing its bytes with the local SHA-256.
2. Reuse an already-approved durable artifact host or existing Sites project.
3. Deploy a dedicated Sites artifact only when production hosting was explicitly authorized in the
   invocation or at the startup gate.

Do not use a local path, `file://` URL, expiring temporary URL, data URL, failed gist URL, issue
comment text, or SHA alone as a durable mockup URL.

Creating a new production Sites deployment is an external write. It is not implied by permission to
create GitHub issues.

## Blocked Publication

If no durable route succeeds:

- create or retain the Plan issue only as a blocked draft when preserving work is useful;
- put `> **Status: BLOCKED - final mockup is not durably published**` at the top;
- record the local path, SHA, attempted routes, and exact failure;
- set final-mockup-publication to blocked in the external-action ledger;
- do not add implementation-orientation or approval-gate wording;
- do not validate Plan as complete;
- emit this repair invocation:

```text
$evidence-gated-delivery plan <plan-issue-url>
```

After the image is durably published, update both issue bodies, run remote readback, audit, and
validation before advancing.
