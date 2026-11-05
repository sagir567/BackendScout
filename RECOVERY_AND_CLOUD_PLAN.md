# BackendScout Recovery and Cloud Delivery

Status: implementation in progress, 31 October 2026.

Superseded as an execution plan by `ARCHITECTURE.md` baseline 1.0 and
`EXECUTION_PLAN.yaml` on 1 November 2026. Retained for recovery history and rationale.

## Outcome

The product should reduce the candidate's work to reviewing a short list and
approving one exact application package. It should say clearly whether an
application was delivered, needs a specific answer, or could not be completed.
Reliable daily discovery is more valuable than another open-ended agent loop.

## What Was Wrong

- Background processes and active schedules were treated as evidence of success.
  A scan could be paused for Gmail authorization while its task was marked done.
- Old high-scoring jobs occupied the digest limit before deduplication, hiding
  fresh candidates lower in the sorted list.
- Multiple source/runtime copies and cloud-evicted local files made imports and
  CV artifacts fragile. Runtime and source changes could drift apart.
- Browser preparation, CV approval, and final submission were separate user
  interactions. Missing files or fields often surfaced after the user approved.
- Notion remains authoritative for business status despite SQLite owning queue
  execution. A Notion outage or schema issue can still block progress.
- The conversational agent can advise but does not yet execute the workflow.
- Generic browser automation cannot make every employer portal unattended.

## Checkpoint One Implemented

The recovery branch fixes shortlist ordering, current-day schedule recovery,
blocked outcomes, mailbox pagination, read-only outcome health, and build-before-
activate deployment. Regression tests cover those paths. Private runtime state
and existing approval requirements remain intact. The standalone commercial
skill is developed separately, with synthetic examples only.

This checkpoint does **not** implement the mobile app, Linux deployment,
single-package approval, or a universal automatic submitter.

## Checkpoint Two Authoritative Application Packages

Keep the approved SQLite/DBOS stack. Introduce a small application service instead
of invoking Typer commands from worker code. CLI, Telegram, and the future web
interface call the same service and receive typed outcomes:
`succeeded`, `blocked`, `retryable_failure`, `permanent_failure`, `uncertain`.

Persist applications and immutable package versions in SQLite. A package contains
the canonical job identity, official destination, CV hash/version, reviewed answers,
delivery route, recipient where applicable, and any email body/attachment hashes.
Approval authorizes precisely that digest; any material change invalidates it.
Keep an append-only approval and delivery-attempt audit.

Use a transactional outbox to project updates into Notion. Reconcile remote edits
as explicit commands; a Notion status change alone is not submission permission.
Migrate a copy first, compare counts and identities, then switch the write owner.

Acceptance: crash/restart tests, changed-package invalidation, replay rejection,
no double delivery, and normal operation while Notion is unavailable.

## Checkpoint Three Private Mobile Interface

Use the approved Python/FastAPI direction and a small responsive web app rather
than a native phone app. First views: Today, Needs My Input, Applications, Health.
One review screen contains the role, truthful fit/gaps, exact PDF, answers, and
delivery route. Telegram becomes a notification and deep-link channel.

Do not expose the UI until authentication, HTTPS, session expiry, CSRF protection,
and private artifact authorization are tested. Never put a CV, bearer credential,
or reusable approval token in a public URL. The mobile app should explain the
specific unresolved question, not ask the user to debug a browser remotely.

Acceptance: real-phone review flow, callback acknowledgements, duplicate clicks,
lost network, stale package, and unauthorized PDF access. No application is sent
merely by opening a link or previewing a document.

## Checkpoint Four Submission Routes

Prefer official sources with workable delivery paths. Classify routes during
discovery: approved email contact, tested portal adapter, manual-only, unavailable.
Do not exclude a strong job silently because its route is manual; show that cost.

After the exact package is approved, deterministic adapters may submit without
another identical approval. Employer ATS write APIs commonly require employer
credentials; a public job-listing API is not a candidate application endpoint.
Use only genuinely supported and authorized applicant routes.

Store an attempt before delivery. Confirm success with receipt/message ID or
visible portal confirmation plus a screenshot/hash. A timeout after clicking is
`uncertain`, not an invitation to retry. Reconcile via confirmation or relevant
mail before another attempt. New eligibility/legal questions return to review.
Site-specific human checks remain a possible blocked outcome; do not promise
unattended completion on every website.

Acceptance: fixture-based adapters, missing attachment, changed form, uncertain
receipt, duplicate attempts, and one owner-approved real application per adapter.

## Checkpoint Five Low Cost Cloud Runtime

Keep the approved small OVHcloud Linux VPS and Docker direction, subject to a
fresh quote and purchase approval. Target combined hosting, backups, and model
usage at no more than 100 NIS/month; this is a budget ceiling, not a verified quote.
Reserve headroom for VAT, exchange rates and disk usage. Start with one machine,
one database, one general worker and one isolated browser lane.

Linux requires a portable secret provider and a fresh Gmail OAuth authorization;
macOS Keychain is not a cloud secret store. Use dedicated browser sessions and
least-privilege secret access, never a copied personal browser profile. Persist
SQLite, CV artifacts and proofs outside disposable containers. Test backup
restore, not just backup creation. Protect backups and limit retention.

Meter model calls per package and per day. Use deterministic collection/scoring,
one structured tailoring call with bounded retries, and no model polling loop.
Cache immutable job extraction; stop paid work at the configured budget limit.
Final model/provider changes still need owner agreement.

Cutover only after a shadow discovery run and a restart test pass. Disable the
Mac scheduler before enabling the cloud scheduler so there is one active owner.
Prove the next morning's digest arrives while the Mac is off. Keep a tested
rollback path without replaying approved or uncertain submissions.

## Dots and Commercial Skill

Dots is an optional orchestration/interface pilot, not a replacement for the
application ledger and verifiable delivery. See `DOTS_ASSESSMENT.md`.

The Agensi pilot exports only the evidence-linked application drafting workflow.
Do not package the live agent, personal configuration, tokens, CVs, or mailbox
data. Publication requires actual delivery testing, payout eligibility, a license,
price, and explicit approval. Marketplace traffic claims are not product validation.

## Next Decision Gate

Finish local recovery acceptance and renew Gmail authorization. Then implement
the application service and package ledger before the mobile UI. Purchase hosting
only after the local container and restore test pass. This sequence keeps existing
discovery available while reducing the number of user interactions incrementally.
