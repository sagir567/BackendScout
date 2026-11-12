# Building BackendScout

## Delivery Proof Checkpoint

Use route-specific evidence when synchronizing confirmed deliveries. A portal
requires its saved receipt screenshot; Gmail requires its returned message ID
bound to the exact recipient, official contact source, CV version and checksum.
Both routes recheck the approved attachment before projection. Never resend an
application to recover a Notion failure. Offline projection tests cover valid
email receipts, mismatched bindings and changed attachments.

Owner-confirmed reusable facts belong in ignored private configuration, with a
confirmation date and scope. They are facts, not authorization to deliver or
accept new terms. Do not publish personal answers in this guide.

This guide explains how to build BackendScout as a complex local-first agent
system. Keep it updated as the project evolves.

CV presentation checkpoint (2026-11-05): keep private authorship evidence intact,
but describe projects through functionality and supported contribution verbs.
Present verified AI and agent tooling in Skills or the summary, not repeated
AI-authorship disclaimers in project descriptions. The tailoring prompt and
project-text validator enforce this boundary; genuine AI product functionality
remains allowed. Previously generated immutable CVs are not silently rewritten
or reapproved when presentation rules change.

Evidence-pause reliability checkpoint (2026-11-08): use the typed `CvEvidenceGap`
to carry the measured score, missing requirements and clarification questions.
The shared service records `needs_input` / `cv_evidence_required`; the legacy
Telegram worker unwraps CLI causes and marks the task `blocked` once rather than
retrying a factual gap. A new owner answer must be validated before updating
private evidence or requesting another draft. Do not equate generic API use or
software tests with prompting expertise or LLM-output evaluation.
When the owner supplies actual examples, retain separate evidence IDs for prompt
iteration and evaluation methodology. Manual conversational checks are qualitative
project experience, not automated evals or measured production performance. Re-run
coverage without changing the target; regenerate an immutable CV and obtain a new
exact-file approval rather than transferring an earlier approval.

## Working from the Frozen Baseline

Read `ARCHITECTURE.md` for the agreed boundaries, interfaces, data ownership and
release outcomes. `EXECUTION_PLAN.yaml` is the maintained work ledger for the
personal and commercial tracks. Keep architecture decisions stable while task
status and evidence change. The older research and recovery plan explain how
the baseline was reached; they are not competing implementation directions.

```sh
uv run --no-editable python scripts/project_status.py --check-evidence
uv run --no-editable python scripts/project_status.py --track personal
uv run --no-editable python scripts/project_status.py --track commercial
```

The validator checks the architecture fingerprint, budget contract, task graph,
unique IDs, completed prerequisites and evidence records. It does not run commands
from YAML or authenticate a claimed test result. An integrator still runs and
reviews the actual acceptance checks before marking a task done.

To advance a task, set `pending` to `in_progress`, implement its acceptance
criteria, run relevant checks, review the diff and attach repository evidence
plus the observed result. Set `done` only after this review. For `blocked`, add
the specific missing action; continue another ready task. Avoid private facts or
account details in the public ledger. Owner/external work stays explicit.

Parallel implementation uses separate write ownership. For example, the package
contract module can be built while the commercial track researches publication
and prepares its release checker. Integration then verifies the contract and
updates shared docs. Two workers should not edit the same service/CLI file at
once. Completed subagent messages are inputs to review, not automatic evidence.

The commercial sibling checkout is required only for checking its local evidence
paths. The personal repository alone can validate and display the plan without
`--check-evidence`. Changes in baseline decisions require an owner-directed
amendment, a version bump and a newly reviewed checksum.

### Package Contract Checkpoint

`application_package.py` implements version-1 review content with Pydantic.
`ReviewedAnswer` and `EmailContent` are frozen nested models; answers become a
sorted tuple. The constructor rejects duplicate keys and defensively copies
caller-supplied values. `canonical_json()` preserves exact text and serializes
keys deterministically; `fingerprint()` hashes the UTF-8 preimage. SHA-256 hex
case is normalized, while meaningful text, URL spelling and line endings are
preserved. Serialization changes require a new package schema version.

Tests include a fixed hash vector, material-field changes, input reordering,
invalid route data, nested mutation and validated copies. This establishes the
data boundary only. The service must still verify the official destination,
actual CV bytes, owner authorization and package currency before delivery.
Continue with P02/P03 rather than treating a constructed package as permission.

### Application Ledger and Linux Foundation

`application_store.py` adds versioned application tables to the existing SQLite
database, using its own migration history instead of overwriting DBOS/queue
tables. Package changes, approvals and delivery intent each write an audit event
and outbox record in the same transaction. File checksums are checked again at
approval and attempt creation. An idempotent replay returns `created=False`;
an adapter must not transmit again for that result. Uncertain attempts need
reconciliation, while a confirmed `not_sent` result permits a new request.

This recorder trusts authenticated service calls and adapter confirmation. It
does not authenticate actors itself or establish that screenshot bytes prove a
portal accepted a job. P03-P07 own those interface and adapter responsibilities.
Migration and backup tests use isolated database copies, not production imports.

Linux selects `GMAIL_CREDENTIAL_STORE=file` and an absolute
`GMAIL_CREDENTIAL_FILE`. The provider validates owner/permissions, rejects unsafe
paths, and replaces bounded JSON atomically. Local use still defaults to Keychain.
Headless authorization uses a fixed callback port and SSH loopback forwarding:
`backend-scout gmail connect --port 8765 --no-browser`. Keep client secrets and
tokens outside Git and never transfer them through a conversation.

The Docker build allows source and example config only. Offline checks have no
network; automation requires an explicit profile plus a sole-scheduler cutover
acknowledgement. Run Chromium/Xvfb and LibreOffice checks before declaring a
platform supported. `CLOUD_DEPLOYMENT.md` describes the host setup and restore
procedure; P13/P14 are prerequisites to P09, not substitutes for full acceptance.

## Local Mobile Workspace

Build the real control surface before renting a server. `web/app.py` is a thin
FastAPI transport; it calls `ApplicationService`, which owns authorization,
package construction, drafting requests and delivery intent. The API never
spawns a CLI subprocess or holds an HTTP request open during CV generation.
The continuous general/browser workers execute `package_cv_draft` and
`package_deliver`. The schedule-enabled worker imports job snapshots every five
minutes without treating Notion's approval/status as local authority.

The four buildless views live in `web/static/`: Today, Needs My Input,
Applications and Health. Required experience, missing full job text, official
destination, current PDF and reviewed answers are visible before approval.
Question answers use a separate operation; answering a form field must not
trigger a paid CV rewrite. Explicit revision feedback reserves the next CV
version through the same truth/coverage/style pipeline used by the CLI.

`web_sessions.py` stores only hashes of random pairing codes, sessions and CSRF
secrets. Only an allowlisted private Telegram owner can issue a five-minute,
single-use pairing code. Browser sessions last twelve hours and use HttpOnly,
SameSite cookies. Every mutation needs the exact configured Origin, JSON and a
session-bound CSRF token. The PDF route is authenticated and rechecks manifest
and artifact checksums instead of serving an arbitrary filesystem path.

Start locally:

```sh
uv run --no-editable backend-scout applications sync --tracker production
uv run --no-editable backend-scout web serve --tracker production
uv run --no-editable backend-scout web pair
```

Open `http://127.0.0.1:8765` on the Mac. For phone access, configure an approved
private HTTPS proxy, set its exact `WEB_PUBLIC_ORIGIN` and enable
`WEB_COOKIE_SECURE`. Keep Uvicorn on loopback; do not substitute a public/LAN
plain-HTTP listener. No VPS, DNS change or public publication is needed for local
development. `system launchd install web --load` is an opt-in fourth Mac service.

Application migration is per job. Importing data does not claim the job; a web
tailoring request or explicit exact-package approval does. Legacy Telegram
buttons, queued legacy preparation tasks and direct legacy CLI delivery commands
must reject a ledger-owned job. This prevents two interfaces from separately
authorizing different CVs. Schema migration preserves existing package authority
and unresolved attempts; take a SQLite backup before runtime rollout.

`WEB_ALLOW_DELIVERY` defaults false. An enabled host queues only an approved
package and creates a durable attempt before invoking its adapter. Missing
questions return typed blocked outcomes; uncertain post-click outcomes require
reconciliation and never automatic replay. Keep a single `PortalSessionManager`
for the browser worker's lifetime: returning a task result must not close its
form or navigate an unchanged session. A worker restart can still lose an
unsubmitted form, so this is not a promise of universal browser persistence.

Verify in layers: isolated service/session/API tests, then the opt-in synthetic
Chromium acceptance test with no real employer or model calls:

```sh
uv run --no-editable pytest
BACKENDSCOUT_BROWSER_SMOKE=1 uv run --no-editable pytest tests/test_web_browser.py
```

The browser check pairs through the actual UI at 360, 390 and 1280 pixels, opens
a valid fixture PDF, checks horizontal overflow, visits Health and verifies
that logout removes CV access. Synthetic artifacts never enter the live tracker.

The image preview is rendered from the checksum-verified one-page PDF, using the
existing PDF rendering stack. Its authenticated URL includes the expected CV
checksum; stale requests cannot silently display a newer file. A small bounded
in-memory cache and a PDFium rendering lock keep preview requests efficient and
thread-safe. Direct PDF open/download remains available.

Identity values are `identity.*` reviewed answers, built from confirmed private
evidence when the package is previewed. Delivery reads those immutable values,
not whichever profile happens to be current later. New contact details therefore
change the fingerprint. The package adapter only fills stable identity fields
and explicit reviewed question answers; custom controls remain unresolved until
a verified mapping is available.

Schema version 3 adds leases, claim tokens and backoff to the Notion projection
outbox. Successful projection overwrites the current page properties, not an
employer action or an append-only duplicate audit. Page parent/tracker and proof
checksums are validated. Current Telegram file notifications are best-effort
after marking the local task; durable notification replay remains P04 work.

For portal acceptance, run `BACKENDSCOUT_BROWSER_SMOKE=1` against
`tests/test_package_portal.py` too. Every HTTPS request is intercepted in an
isolated browser profile, so the fake Ashby form exercises real uploads and
submit/confirmation behavior without contacting an employer. This test caught
a stale pre-upload required-field result that incorrectly reported a missing
resume even after a successful attachment. Audit the current post-upload state,
not an earlier list of gaps.

Local rollout on 2026-11-02: a private SQLite backup preceded migration; the
production workspace imported 69 job snapshots, verified 20 existing CVs and
found five complete reviewable packages. No old approval was transferred and no
employer was contacted. The web listener is loopback-only and the original daily
scout/mailbox schedules remain active. Private phone HTTPS setup is separate.

Verified release `20261102T084955773936Z`: all four Mac services and both
original schedules are loaded, the five-minute application sync recorded a
successful outcome, and unauthenticated overview access returns 401. The final
standard suite passed 1,086 tests (seven opt-in browser cases skipped); the
explicit UI/portal acceptance command passed all 15 selected cases. Ruff and
offline lock validation passed. A single-use owner code was delivered privately
through Telegram, without writing its plaintext to logs or this guide.

The phone ingress check on 2026-11-02 confirmed an online Mac and iOS device
under the same owner, with no Tailscale health warnings. Serve had no configured
routes and required owner enablement through its consent page. The command was
bounded and stopped while awaiting consent; the live app origin was kept local
while that approval was pending. Device login is not proxy readiness. Never
turn on Funnel to work around this gate. Follow-up tests verify Secure owner
cookies, rejection of insecure HTTPS configuration, and exact-origin CSRF
through a synthetic HTTPS host; 50 session/API tests passed.

After owner consent, private Serve was activated for HTTPS 443, proxying only
to the existing `127.0.0.1:8765` listener. The resulting Serve configuration had
no public Funnel enabled. No application task or ledger-owned browser session
was active before reloading release `20261102T092933530618Z`. The private origin
is stored only in ignored `.env`; `WEB_COOKIE_SECURE=true` is enabled there.
Do not publish the owner's actual device name or tailnet address in this guide.

Live checks from the Mac verified normal TLS certificate validation, rejection
of unauthenticated access, a rejected old-loopback Origin, successful owner
pairing with Secure cookies, authenticated access to 69 application snapshots,
and access revoked after logout. The verification session was revoked and a
fresh five-minute code sent to the owner's private Telegram chat. All four Mac
services and both original schedules remained active. The full isolated suite
passed 1,088 tests with seven opt-in browser cases skipped; Ruff and lock checks
passed. The owner still performs the final Safari-on-iPhone usability check;
an online iPhone and a successful Mac request do not prove that browser step.

## Experience In Reviews

### Usability And Source Enrichment Checkpoint

A live read-only audit found 37 title-only LinkedIn snapshots among 69 tracker
records. The web detail view was also hiding existing PDFs behind missing
application-route prerequisites. These are different readiness concerns:
`cv_artifact` and its checksum-bound image remain available even when
`preview_package` cannot construct an approvable delivery package.

Keep the main controls above the full posting/manual editor, with required
experience visible before them. The explicit commands are Create CV, View CV,
Request changes and Approve & apply. A missing-data editor must not auto-open
as the primary workflow. Return a structured `package_blocker` so the interface
distinguishes no PDF, missing source text, missing official route, active work
and historical or uncertain delivery. Do not show internal identity field names
as user-facing labels.

`package_enrich` is a read-only general-lane task. A Create CV request for an
incomplete posting queues it with an explicit follow-up drafting intent; a
Fetch job details request does not. The worker rejects historical/in-flight
changes, invalidates an old package before refreshing material facts, preserves
job identity and retains the original source. Public exact-posting APIs cover
Greenhouse, Ashby and Lever including EU. Supported board HTML uses matching
JobPosting JSON-LD or identifiable public LinkedIn markup, never generic page
text, login placeholders or invented requirements. The bounded HTTP boundary
uses HTTPS, approved hosts, safe queries, constrained redirects, time/request
and decoded-body limits, and no candidate credentials or cookies.

Successful source retrieval recomputes skills/years and transparent matching;
it does not approve a job, update career evidence or submit anything. Unavailable
sources become precise blocked outcomes without invoking the CV model. CV
generation can finish without an official application URL, while submission
continues to require that verified route and a fresh exact-file approval.
Pending work is polled at 2.5-second intervals only while visible and not editing;
the page observes source/CV completion without a manual refresh.

Pairing delivery uses a typed BrowserPairingMessage with a repr-hidden code.
Both the owner-only Telegram command and CLI send instructions and a code-only
message through one shared helper. Authorization, single-use expiry, hashed
storage and group-chat rejection remain unchanged. Regression tests assert two
messages and no plaintext code in instructions or CLI output.

Verification includes isolated public API/HTML fixtures, missing-data-to-CV
browser flow, existing-PDF access without a route, no-model fetch-only behavior,
and unsupported/closed/authenticated source handling. A live read-only Mobileye
example retrieved 1,478 description characters from the public EU Lever API; a
separate LinkedIn example correctly reported a public-access gate. Neither live
check wrote job data, generated a CV or contacted an employer.

Deployed usability release `20261102T131632900088Z` on 2026-11-02 after
checking that no application task or delivery attempt was running. All four
Mac services and the original scout/mailbox schedules passed the runtime
doctor. A temporary owner session over private HTTPS verified authenticated
PDF and PNG access for an incomplete posting, an explicit submission blocker,
the updated controls, rejected unauthenticated access and successful logout.
The test session was revoked; a fresh owner code was delivered as a separate
Telegram message. No job data or CV artifact was changed by these live checks.

Final verification passed 1,206 standard tests with seven opt-in cases skipped,
all 15 explicitly selected synthetic browser/portal cases, Ruff, offline lock
validation and architecture/evidence validation. Phone layouts were checked at
360 and 390 pixels and desktop at 1280 pixels. The actual phone's refreshed
browser remains the owner's final usability check; synthetic tests do not prove
universal portal support. Durable notifications, broader delivery adapters and
full collector/mailbox ledger migration remain unfinished work.

Carry `Job.years_experience` through the existing Notion Years Experience
property into `ApplicationDigestItem.years_experience`. Render it near the
score on every Telegram digest and in job/status lists, before any approval
button is offered. Preserve source wording such as 6+ years or a range;
missing information must explicitly request full-posting verification.
Unknown experience is not evidence of a junior role, and a high skill score
does not erase a seniority requirement. Tests round-trip the value through
Notion properties and check both stated and missing requirements. No new
dependency, model call or automatic rejection policy is introduced.

### Proposed Autonomous Delivery Readiness

Research checkpoint on 2026-11-02, not a deployed feature: separate professional
matching from delivery capability. A registry host match is only an inspection
candidate, not proof that an exact form can be submitted unattended. A future
`Agent submits after approval` filter should require a current verified route,
supported form contract, complete reviewed answers and an exact verified PDF.
Record reasons and observation time; new blockers downgrade readiness rather
than repeatedly retrying or manufacturing answers. Readiness never authorizes
delivery by itself.

Official ATS documentation distinguishes public job reads from authenticated
write APIs: Greenhouse and Lever need employer-side submission keys; Ashby's
`applicationForm.submit` needs API authentication and `candidatesWrite`. A public
feed is not a generic candidate write API. Comeet documents position-specific
application email addresses, but its aggregate job-board feed needs partner
access. Use an address only when verified for the exact job from an authorized
source, never generated from a pattern. Indeed and LinkedIn's own automated
Apply paths are unsuitable under their documented automation restrictions.

Prefer the existing Gmail integration for explicitly advertised application
email routes and tested official browser forms otherwise. Unified email packages,
proof types, exact recipient/body approval and uncertain-send reconciliation
remain P07 implementation work. Gmail acceptance proves sending, not employer
receipt; preserve that distinction in both status and notifications. Source
links and the detailed proposed acceptance slices are recorded in the ignored
local `docs/AUTO_APPLY_ASSESSMENT.md`. No delivery behavior or architecture
baseline was changed by this research.

### Delivery Capability And Public Form Observation

The next implemented slice separates professional match from operational
capability. `delivery_readiness.py` is a pure, conservative classifier.
`Agent Apply` groups active Ashby, Comeet, Greenhouse and Lever destinations,
while each role displays its current readiness and missing prerequisites.
Unknown hosts and LinkedIn are not automatic candidates; historical and
unresolved delivery states are excluded. The screen is not a guarantee that a
recognized host can be submitted unattended.

Only exact current PDF/package facts plus a recent, bound browser inspection
can satisfy `supported_after_approval`. The current UI integration deliberately
does not manufacture that observation: a public API result is insufficient.
Inspection freshness is separate from approval validity. Completing the browser
preflight producer and unified email delivery remains P07 work.

`greenhouse_forms.py` performs only the documented unauthenticated job GET with
`questions=true`, using the bounded enrichment HTTP boundary. Verify exact job
ID, title and company before interpreting fields. Preserve question-level
required flags and alternatives; file OR text fields under one resume question
must not be interpreted as two mandatory uploads. Malformed, conditional or
unsupported compliance schemas return an explicit unavailable result, not a
partial all-clear. The parser has no POST, candidate data, model or credential
interface. Greenhouse application POST still requires the employer's API key.

The owner-authenticated, CSRF-protected `inspect-form` endpoint queues a
`package_form_inspect` task; it never fetches the source during the HTTP request.
The general worker records the typed result in schema-v4
`application_form_inspections`, keyed to the current official destination.
Changed destinations hide stale observations. Inspection does not claim a job,
approve its package, alter lifecycle or create a delivery attempt. The phone
polls while a form check is pending and displays its result. Roll out all workers
and web together after a consistent SQLite backup; schema-v3 code is not a
rollback target for an already-upgraded database without restoring its backup.

### Wake-Time Scouting Recovery

The 2026-11-03 production scout exhausted three attempts after a DNS error.
Its existing per-day idempotency key correctly prevented duplicate scheduling
but also kept it terminal after connectivity returned. The watchdog now retains
the original failure audit and queues one separately keyed connectivity recovery
for that day after thirty minutes. Only recognized transient network failures
qualify; success, manual cancellation, configuration failures and historical days
do not. Ordinary task retry/backoff and discovery deduplication remain active.
This does not wake a powered-off Mac or retry an application submission.

Observed operations: the 2026-11-03 recovery completed successfully; the original
2026-11-04 morning scout also completed successfully and mailbox health was good.
Four owner-supplied roles were verified and imported without approvals. Relative
posting dates and externally supplied scores were not adopted as facts; graduate
eligibility and unknown work arrangements remain explicit. No CV was generated,
uploaded or submitted during those operations.

Verified rollout on 2026-11-04: release `20261104T083136888970Z` followed a
consistent owner-only SQLite backup and an idle scout/CV/delivery check. All
four services passed the deployment doctor. Live private HTTPS verification
confirmed owner-only access, the five-tab assets, 88 production snapshots, all
four imported roles and four supported-route candidates. The temporary test
session was revoked; four previously unsent review cards reached Telegram.
The live public reader reported two old Greenhouse destinations as unavailable
without attempting delivery or changing their lifecycle.

The final isolated suite passed 1,530 tests with seven opt-in cases skipped;
all 15 explicitly selected browser/portal cases passed at phone widths 360/390
and desktop 1280. Ruff, offline lock validation and architecture/evidence checks
passed. No new provider, model call, application delivery, commit or push was
part of the rollout. Application email migration, durable notifications and
complete browser preflight remain unfinished and are not advertised as deployed.

### Missing Posting Regression

On 2026-11-04 an owner-approved destination returned a public API 404 and its
hosted link redirected to an error page. All recorded browser attempts stopped
before transmission. The defect was integration: negative form observations
were displayed but did not influence readiness, review controls or delivery
gates, allowing repeated futile Resume requests and misleading Agent Apply
membership.

Keep artifact readiness separate from route availability. The exact PDF and
package remain viewable; `posting_observation_blocker` supplies a typed route
blocker instead of deleting files or asserting that the whole vacancy closed.
Apply it consistently to the shortlist, detail view, owner approval, resume,
already-queued delivery and drafting. Lazy public Greenhouse checks run only in
workers before model work and before delivery, not during a web GET or approval
HTTP request. No employer key or candidate transmission is involved.

Use the latest decisive observation for the current destination: a later timeout
or unknown schema cannot erase a known missing link. A successful check can
clear only a prior route-specific input block; it never sends an application or
creates approval. Changed destination URLs hide old observations and change the
package fingerprint. The public inspector now treats the reviewed application
URL as its target, preserving an old discovery URL only as provenance, while
still verifying returned job ID, title and company.

The phone view disables apply/resume and repeat drafting for a known unavailable
route, provides an Update application link action and keeps View CV enabled.
Explicit CV retry also avoids reusing an exhausted terminal task as if it were
queued; simultaneous owner clicks still share active drafting work. These rules
do not retry employer transmissions, relax redirect checks or infer sensitive
answers. Regression coverage includes preserved CV access, no new approval or
delivery attempt, inconclusive rechecks, URL replacement, stale approval and
phone/desktop controls.

Release `20261104T090623532998Z` passed a consistent private backup/idle-work
check and runtime doctor. Live private HTTPS negative-path testing verified
that both approve and resume returned `posting_unavailable` for the affected
saved link, with zero added delivery attempts, the exact approval retained and
the PDF still accessible. The active supported-route count dropped from four
to three; the row remains available outside that filter rather than being
deleted or falsely marked submitted/closed. An owner-only Telegram explanation
was delivered. No employer was contacted.

Final verification: 1,549 standard tests passed (seven opt-in cases skipped),
all 15 separately selected UI/portal cases passed, and Ruff, offline lock and
architecture/evidence checks passed. The controlled browser checks include
360/390/1280 viewports, disabled apply controls, preserved PDF access and the
replacement-link editor. No commit or remote push was made.

## Closed Portal Recovery

A persistent Playwright profile saves browser session data, not unsaved DOM
form values or file-input attachments. The current preparation command closes
its browser context after returning, including incomplete forms. It is not a
long-lived interactive-browser owner; do not describe it as one.

For recoverable user access, `/submit_status` now includes Open in browser,
and `/open_FULL_NOTION_PAGE_ID` returns a reusable link after checking sender
authorization and tracker identity. Neither action queues application work,
changes CV approval nor marks an application submitted. Submitted/uncertain
records carry a warning to inspect any existing receipt before applying again.
Only normal HTTP(S) entry URLs without embedded credentials/tokens are offered;
sign-in/authwall destinations fall back to the discovery URL. Portal preparation
must not overwrite an employer URL with a temporary login redirect.

Authentication is detected for non-LinkedIn portals too. The user can resume
in their regular browser with their existing login, independent of a worker's
lifetime. The workflow still needs the planned browser-session ownership work
for reliable continuation of automatic multi-step forms. Link recovery alone
does not restore unsaved values, prove receipt or authorize a new submit click.
Tests cover link validation, read-only recovery, unauthorized/cross-tracker
commands, password gates and unchanged application status.

## Scouting Coverage Repair November 2026

A healthy scheduler does not prove discovery coverage. The live audit found
that Lin-Srael searches only fetched page one: the Backend query advertised
186 results over four pages and Software Engineer advertised 471 over ten.
The original shortlist contained 13 matches, including 12 applications that
had not reached submitted status. Calling every tracked record already
reviewed concealed that backlog.

The repair keeps matching deterministic and approvals unchanged:

1. Follow the public API's `pages` metadata; without metadata, stop at a short
   page. Deduplicate overlapping results, detect repeated pages and bound each
   term with `TargetCompany.max_pages` (default 20). Report incomplete collection
   instead of silently treating it as exhaustive.
2. Match title filters as Unicode words/phrases. Preserve C++/C# and .NET
   distinctions, and normalize back-end/backend without substring exclusions
   such as BI accidentally matching Billing or Mobile.
3. Recognize Python Developer as well as Python Engineer. Treat Java/C#/.NET
   developer titles with backend evidence as adjacent software roles, while
   not promoting QA titles merely because they mention Python.
   Feed placeholders without detailed requirements remain preliminary `maybe`
   matches even when title/skill points are high, with an explicit warning.
4. Report per-source volume, first-reason filter counts and full paginated
   tracker-state counts in the daily summary. Tracked does not mean applied.
5. Use `collect run --write-notion --send-digest --resend-pending` for an
   explicit recovery digest. Only jobs awaiting tailoring approval can be
   resent, after new discoveries and within the configured digest cap.
   Existing approvals, CV versions and submitted/closed states are preserved.

Regression tests in `test_collectors.py`, `test_scouting_preferences.py`,
`test_matcher.py`, `test_cli.py` and `test_recovery.py` cover these rules. Live
audit output belongs under ignored `docs/`, never in public candidate data.
An onsite-work preference is a user choice, not a bug to silently override.
After the owner explicitly allows office-only jobs, change only the private
profile's `work_preferences.onsite` value, validate it and deploy that config to
the active runtime. The existing scorer awards six work-mode/location points
for a matching onsite location versus ten for a fitting hybrid/remote role.
Regression tests cover both allowed and disallowed onsite preferences, and
Telegram's `Work mode` label shows the supplied policy without inferring days
at home when the posting does not specify them.

## Recovery Checkpoint October 2026

Before adding another agent, make operational failures visible. A live process
is not proof that a job search or mailbox scan succeeded. The recovery changes
are deterministic and add no model calls or dependencies:

- `runtime_health.py` reads production outcomes through read-only SQLite. A
  scout is stale after 26 hours; mailbox success after 50 minutes. Missing state
  is unhealthy and must not create an empty database by accident. This check
  cannot retroactively correct older tasks that were wrongly marked done.
  Queued/running work does not clear a blocked outcome or a failed retry; a
  subsequent successful outcome is required to restore health.
- `QueuedTaskStatus.BLOCKED` distinguishes a missing authorization or unresolved
  form from success. Blocked tasks are not claimed repeatedly by workers.
  Explicit user commands may enqueue a fresh attempt after the issue is fixed.
- Explicit schedule idempotency keys survive terminal task status. A watchdog
  checks once a minute after 08:00 Asia/Hebron and recovers only today's missing
  scout. DBOS backfill of old morning digests is disabled. A completed/blocked
  scheduled run is not recreated endlessly; manual retries use a fresh key.
- `select_reviewable_jobs` checks the tracker before taking the top N. Otherwise
  already-reviewed high scores can permanently hide new lower-scored matches.
  A row left in `found` after failed message delivery remains reviewable.
  Batch selection also deduplicates the company/title identity used by Notion's
  fallback lookup, before creating rows from different discovery sources.
- Mailbox matching uses every Notion page, not just the first 100 applications.
  A repeated/missing pagination cursor fails explicitly instead of looping.
- Release deployment copies a versioned directory, builds its own locked venv,
  and checks CLI startup before switching `current`. A failed build leaves the
  previous pointer and manifest untouched. Shared private state is preserved.
  Service restart failures after activation still require operator attention;
  this is not transactional rollback of every LaunchAgent.

Run the regression suite with:

```sh
uv run --no-editable pytest
uv run --no-editable ruff check .
uv lock --check
```

`tests/test_recovery.py` covers stale outcomes, blocked Gmail and portal work,
digest selection, pagination, schedule deduplication and current-day recovery.
`tests/test_runtime_bundle.py` injects a build failure and checks the previous
release remains active. These tests mock external APIs; they do not establish
that any real employer portal accepted an application.

Learning exercise: make a collector unavailable, then compare a worker's task
status, the Telegram summary and the health report. An empty successful search
and a search that could not check any source must not look like the same outcome.
Use the test tracker; do not test by submitting to real employers.

The remaining migration and acceptance gates are in
`RECOVERY_AND_CLOUD_PLAN.md`. Keep the current approval gates until the single
package approval is implemented and verified; a design document cannot grant
a live submission authorization.

## What We Are Building

BackendScout is a job-search agent for backend engineering roles. It should:

1. Scan relevant job sources.
2. Parse and normalize job descriptions.
3. Score each role against a truthful candidate profile.
4. Send a daily digest for human review.
5. Tailor CV drafts only after approval.
6. Submit applications only after final approval.
7. Track every job and application in Notion.
8. Generate interview preparation tasks for approved applications.

## Core Design Principles

- Human approval is part of the architecture, not an afterthought.
- SQLite is the operational source of truth for task execution, schedules,
  inbox idempotency, and agent sessions. During the staged migration, Notion
  remains the business-status tracker; an outbox-driven projection is the next
  storage migration, not a capability claimed by the current release.
- The CV archive stays outside this repo and is configured locally through
  `CV_ARCHIVE_ROOT`.
- The system must be truthful and interview-explainable.
- Each agent should have a narrow job and structured inputs/outputs.
- External actions should be wrapped in explicit tools.
- Every important state transition should be observable in Notion.

## Current Architecture

```text
Durable Scheduler (DBOS)
  -> Job Collectors
  -> Job Parser
  -> Matcher
  -> Telegram Digest
  -> Human Approval
  -> CV Tailor
  -> Final Human Approval
  -> Submitter
  -> SQLite Workflow State
  -> Notion Projection
  -> Prep Coach
```

## Durable Runtime Migration

The first migration checkpoint replaces the JSON queue with a SQLite queue that
supports atomic claims, worker leases, retry backoff, dead-letter tasks,
idempotency keys, and an append-only task event log. JSON paths remain supported
only for compatibility tests and one-time history import.

DBOS stores durable schedule execution in the same private SQLite database. The
daily production scout and 15-minute mailbox schedule enqueue idempotent tasks;
the task worker stays alive and checks for work every second.

Browser preparation and submission run in a dedicated single-concurrency lane.
The general worker owns schedules, scouting, mailbox scans, CV drafting, and
contact discovery, so a portal waiting for attention cannot block the morning
digest.

Telegram update IDs are journaled in SQLite. Completed updates are idempotent,
failed updates retain their error, and one bad update no longer stops the rest
of the fetched batch.

Authorized free-text Telegram messages and `/ask ...` are queued as inexpensive
agent-chat tasks. The OpenAI Agents SDK stores conversation history in SQLite,
but the agent has no execution tools: it can explain, ask for facts, and propose
next steps while deterministic commands retain all state-changing authority.

Migration commands:

```bash
uv run --no-editable backend-scout tasks migrate-json
uv run --no-editable backend-scout tasks worker --enable-schedules
```

The legacy daily and mailbox LaunchAgents are retained as manual compatibility
options but are no longer part of `launchd all`. DBOS owns both schedules in the
normal runtime, preventing duplicate morning and inbox runs.

The browser migration uses one profile directory per Notion page and records a
prepared-page fingerprint, CV checksum, adapter name, and review screenshot.
Only Greenhouse, Comeet, and Lever have final-submit adapters initially. An
unknown portal can be prepared for review but must not use a generic submit
button. A click without verified confirmation enters `submission_unknown` and
is never retried automatically.

LinkedIn public job pages use an explicit authentication checkpoint. A visible
sign-in wall is reported as `linkedin_authentication`; hidden login controls
are never treated as application fields. Once the candidate has signed in
within the per-job browser profile, the adapter recognizes only an exact final
`Submit application` control and a visible delivery confirmation.

Runtime deployment creates an immutable directory under `releases/`, links its
`data` and `private` paths to shared state, installs dependencies inside that
release, and atomically moves the `current` symlink only after the files are
complete. LaunchAgents execute through `current`, eliminating source/runtime
drift while preserving rollback-ready older releases.

When the source checkout has an active deployment, the CLI activates the same
canonical `private/` paths before loading settings. This prevents a CV created
interactively from being invisible to the Telegram callback worker. Run
`backend-scout system runtime doctor` after deployment; it verifies all four
state paths plus the loaded Telegram, general-task, and browser workers.

## Repo Files To Keep Updated

- `README.md`: current setup, commands, and project state.
- `SYSTEM_BUILD_GUIDE.md`: learning guide and build instructions.
- `REQUIREMENTS.md`: system requirements, install commands, and external services.
- `requirements.txt`: pip-compatible entry point for installing from `pyproject.toml`.
- `AGENTS.md`: rules for Codex and future agents working in this repo.

Local-only notes belong in `docs/` and are not committed.

The architecture research and migration rationale are committed in
`ARCHITECTURE_RESEARCH.md`.

## Phase 1: Foundation

Goal: make the repo runnable and connect the first external system.

Completed:

- Created `BackendScout` as a separate git repo.
- Added approval and documentation rules in `AGENTS.md`.
- Chose a Notion-first tracker after comparing storage approaches.
- Chose `uv` as the official Python/project environment manager.
- Connected a Notion `Applications` data source.
- Added Notion schema validation.
- Added root-level runtime requirements documentation.
- Pushed the foundation to GitHub after explicit approval.

TODO:

```text
- [ ] Install uv.
- [ ] Run uv sync --extra dev --no-editable --link-mode copy.
- [ ] Run uv run --no-editable backend-scout notion check.
- [ ] Create the real candidate profile file from config/candidate_profile.example.yaml.
```

macOS note for this machine:

If the repo stays under `Documents` and `.venv` inherits File Provider metadata,
keep the real virtualenv outside `Documents` and leave `.venv` as a symlink:

```bash
mv .venv .venv.documents-backup-2026-08-31
uv venv /private/tmp/backendscout-venv
ln -s /private/tmp/backendscout-venv .venv
uv sync --extra dev --no-editable --link-mode copy
```

## Phase 2: Candidate Profile

The candidate profile should be structured, truthful, and easy to inspect.

Inputs:

- Target roles.
- Locations and remote/hybrid preferences.
- Salary floor or compensation preferences.
- Real skills.
- Real projects and experience.
- Constraints, such as roles to avoid.

Output file:

```text
config/candidate_profile.yaml
```

This file is ignored by git because it contains real personal data.

TODO template:

```yaml
name: TODO
target_roles:
  - Backend Engineer
target_locations:
  - TODO
salary_floor_nis: TODO
work_preferences:
  remote: true
  hybrid: true
  onsite: false
core_skills:
  - TODO
proof_points:
  - TODO real project or work example
constraints:
  require_truthful_cv_only: true
  require_approval_before_submit: true
```

Validation command:

```bash
uv run --no-editable backend-scout profile check
```

## Phase 3: Notion Tracker

Notion is the human-facing tracker. During the durable-runtime migration it
remains the compatibility tracker; afterward SQLite owns workflow state and an
outbox synchronizes reviewed changes to Notion. Critical state changes always
require the same approval rules regardless of storage.

Required properties:

| Property | Type |
| --- | --- |
| Role | Title |
| Company | Text |
| Status | Status |
| Source | Text |
| Source URL | URL |
| Application URL | URL, required before portal preparation |
| Location | Text |
| Remote Policy | Text |
| Employment Type | Text |
| Salary | Text |
| Match Score | Number |
| Required Skills | Multi-select |
| Years Experience | Text |
| Match Reason | Text |
| Description | Text |
| Discovered At | Date |
| Submission Channel | Select, required only before real delivery |
| Contact Source | Text, required only before real delivery |
| Submission Record | Text, required only before real delivery |

Use `notion add-submission-fields --tracker production` to add the application
URL and delivery-only columns to the production data source. It is idempotent
and refuses type conflicts.

Validation command:

```bash
uv run --no-editable backend-scout notion check --tracker test
```

## Phase 4: Job Collection

Start simple. Do not scrape everything at once.

Collector order:

1. Manual import from pasted URL/text.
2. Public company career pages.
3. Greenhouse/Lever/Ashby-style public job pages and reviewed Israeli aggregators.
4. Browser-assisted sources only when needed.

Rules:

- Do not bypass CAPTCHA.
- Do not bypass login restrictions.
- Respect job-site terms and rate limits.
- Store only the data needed for matching and tracking.

TODO:

```text
- [x] Add manual import command.
- [x] Add normalized Job model fields for parser output.
- [x] Add dedupe key strategy.
- [x] Add first public collector.
```

Manual import commands:

```bash
uv run --no-editable backend-scout jobs validate examples/manual_job.example.yaml
uv run --no-editable backend-scout jobs import examples/manual_job.example.yaml
uv run --no-editable backend-scout jobs import data/raw/company-role.yaml --write-notion
```

The import command is preview-only unless `--write-notion` is passed.

Public ATS collector progress:

- [x] Add Greenhouse, Lever, and Ashby collectors that use published job-board
  JSON only; none calls an ATS application endpoint.
- [x] Add Lin-Srael as a low-volume, keyword-configured Israeli discovery
  source. Preserve its listing URL and use only a published external employer
  URL as the application target.
- [x] Add a private `target_companies.yaml` worksheet with enabled/disabled
  boards and Israel-relevant starter companies.
- [x] Filter collection results to Israel locations or remote roles before
  transparent scoring and Notion sync.
- [x] Extract deterministic skill and years-of-experience signals from public
  posting descriptions so ATS feeds without structured requirements still score
  usefully.
- [x] Keep the automatic shortlist high-precision by requiring meaningful
  target-role relevance before a collected job enters the digest.
- [x] Add optional private `scouting_preferences.yaml` controls for preferred,
  maybe, and excluded title keywords plus minimum score/relevance thresholds.
- [x] Add a `launchd` template that invokes the same tested CLI command used
  for a manual daily collection.
- [x] Add `system launchd install/status/uninstall` commands so the local
  Telegram listener, worker, mailbox watcher, and daily scout can be installed
  from the same verified CLI.
- [x] Add a reviewed Israeli aggregator source with a public feed, source limits,
  and cross-query deduplication.

Current import behavior:

- `jobs score` gives a deterministic shortlist view without external writes.
- `jobs import --write-notion` rescoring is always based on the latest local
  candidate profile.
- Duplicate copies of the same job in one YAML file are skipped during sync.
- Existing Notion rows are updated in place when the same job is imported again.
- Existing workflow status is preserved during updates so approval gates stay intact.

## Phase 5: Matching Agent

The matcher should return structured reasoning, not just a score.

Expected output:

```json
{
  "match_score": 0,
  "fit_summary": "TODO",
  "matched_skills": [],
  "missing_skills": [],
  "concerns": [],
  "recommended_action": "skip|maybe|apply"
}
```

Scoring should consider:

- Backend relevance.
- Required experience.
- Skills overlap.
- Location and remote policy.
- Salary floor versus stated compensation.
- Company/domain interest.
- Salary if provided.
- Risk of mismatch or overreach.

Current progress as of 2026-08-31:

- [x] Deterministic scoring engine with transparent weighted breakdown.
- [x] Approval-gated status flow modeled before any future submission work.
- [x] `jobs score` CLI for shortlist-style review.
- [x] `jobs import --write-notion` now recomputes `Match Score` and `Match Reason`
  from the local candidate profile before syncing Notion rows.
- [x] Repeated imports dedupe identical jobs and update existing Notion rows.
- [x] Telegram digest send and one-shot approval polling over the raw Bot API.

## Phase 6: Human Approval Loop

Telegram will be the first approval channel.

Digest format:

```text
Company - Role
Score: 87%
Location: TODO
Remote: TODO
Key requirements: TODO
Why it fits: TODO
Action: approve / skip / maybe / details
```

Approval gates:

- Before tailoring a CV.
- Before sending or uploading a CV.
- Before sending recruiter messages.
- Before marking an application as submitted.

Current implementation as of 2026-08-31:

- `telegram check` verifies the bot token and prints configured allowed user IDs.
- `telegram peek-updates` shows pending Telegram user IDs and messages without
  touching Notion state or the stored offset.
- `telegram send-digest --chat-id <id>` pulls jobs from Notion by status and
  sends one digest message per job.
- Digest messages include inline buttons for `Approve tailoring` and `Close`.
- Sending a digest moves `found` jobs to `digest_sent`.
- `telegram poll-once` consumes Telegram callback updates once and stores the
  last processed Telegram update ID in `data/telegram/last_update_id.txt`.
- `telegram listen --tracker production` continuously long-polls the production
  workflow. `scripts/run_telegram_listener.sh` and its `launchd` template keep
  it alive across terminal sessions and write only local ignored logs.
- The listener processes only authorized callback buttons and the explicit
  `/tailor_<page-id>`, `/revise_<page-id>`, `/draft_<page-id>`,
  `/prepare_<page-id>` fallback, `/status`, `/jobs`, `/today`, `/submit_status`, and
  `/scout` commands. Free-form messages do not authorize delivery or external
  submission.
- `/jobs` lists actionable open applications, `/today` lists recent tracked
  jobs, and `/submit_status` lists jobs ready for portal preparation or final
  submit progress.
- Telegram callback queries are acknowledged immediately before slower Notion,
  Gmail, or browser work starts, so buttons should stop blinking quickly.
- `Approve tailoring` can create a local queued CV draft task. Approving that
  exact CV returns an inline `Prepare portal` button, and `/submit_status`
  resends `Prepare` buttons for CV-approved jobs and `Resume` buttons for
  partially prepared forms. This avoids Telegram truncating long, hyphenated
  Notion IDs. The typed `/prepare_<page-id>` fallback validates the complete ID
  before queueing. `tasks worker-once` processes one task at a time; the
  launchd worker runs the same command every minute with a local lock.
- Only configured Telegram user IDs may trigger approval actions.
- `Approve tailoring` transitions `digest_sent -> approved_to_tailor`.
- `Close` transitions the current job to `closed` when that transition is valid.
- Automated tests cover digest formatting, inline buttons, authorized and unauthorized
  callbacks, the `found -> digest_sent` writeback, and the CLI handoff.
- A live test on 2026-09-01 confirmed the configured Notion test table can create,
  score, and send a test digest to the configured Telegram chat. The final callback
  transition remains intentionally human-triggered by pressing a Telegram button.

## Phase 7: CV Tailoring

The CV tailor can reorder, clarify, and emphasize real experience. It cannot
invent facts.

Inputs:

- Candidate profile.
- Base CV source.
- Job description.
- Match analysis.

Outputs:

- Tailored CV draft.
- Change summary.
- Risk report: anything that might sound unsupported.

Approval:

```text
No CV artifact is submitted or archived as submitted until the candidate approves it.
```

Current implementation as of 2026-09-01:

- A private `config/career_evidence.yaml` ledger is the only permitted source for
  CV claims. The committed example contains placeholders only.
- The OpenAI Responses API returns a structured draft with evidence IDs attached
  to every summary and bullet; unsupported skills or citations are rejected.
- `cv draft <notion-page-id>` only works from `approved_to_tailor` and produces
  local DOCX and PDF files under the configured private archive root.
- The artifact manifest stores a SHA-256 digest. Telegram's `Approve this CV`
  action verifies that exact digest before transitioning to `approved_to_submit`.
- The model response uses strict structured output. Every generated statement is
  tied to private evidence IDs before document rendering.
- A private `config/cv_style.yaml` presentation contract is included with the
  model input and enforced again by the DOCX renderer. It currently hides the
  headline under the candidate name and prohibits visible raw URLs.
- DOCX contact and project links render as labeled external hyperlinks, such as
  `GitHub` and `LinkedIn`, rather than printing full URLs.
- Attachment filenames are derived from the private candidate identity as
  `<candidate name> CV.docx` and `<candidate name> CV.pdf`; personal identity is
  not hardcoded in the public source tree.
- Keep a role heading separate from its organization/date line. A fixed right
  tab stop can concatenate dates with a long role title in the PDF even when
  the DOCX XML contains a tab. Test the explicit line break and visually render
  long real headings; text extraction and a one-page count alone cannot catch it.
- The final PDF must be exactly one page and use at least 90% of the usable
  page height. BackendScout measures the rendered PDF locally with pdfplumber,
  selects the fullest valid layout candidate, and fails before delivery if it
  cannot satisfy both conditions. If typography scaling jumps from a sparse
  page to two pages, a second deterministic pass stretches vertical spacing
  while preserving font size, line wrapping, and factual content.
- Skill labels returned by the model are intersected with the verified evidence
  list before validation, so unsupported labels are omitted rather than shown.
- Before drafting, `cv coverage <notion-page-id>` measures the factual evidence
  ledger against the job's explicit requirements. Its 90/100 target is separate
  from the match score: tailoring can improve presentation, but it cannot make a
  job more suitable or introduce unsupported qualifications. Below target, CV
  creation pauses by default. Set `CV_REQUIRE_COVERAGE_TARGET=false` to draft
  without closing the evidence gaps. Test drafting and submission readiness
  independently: complete requirements can support a draft, but a verified
  route is still required for an approvable package. Confirmed answers are
  added to the private evidence ledger; a skill gap remains open until backed by
  real work or a completed project.
- The first version presents coverage questions in the active conversation. Its
  result is transport-neutral so Telegram can send the same questions and store
  the answers in a later approval-loop slice.
- The Telegram document sender uses multipart uploads and redacts request URLs
  from failures so a bot token is never printed in an error message.
- A live test created and delivered a one-page DOCX/PDF pair to the configured
  Telegram chat, then verified the exact artifact checksum and `cv_drafted`
  Notion status. Final approval remains human-triggered.
- A live black-box acceptance test on 2026-09-02 exercised a fictional backend
  job through scoring, Notion import, Telegram digest approval, OpenAI drafting,
  DOCX/PDF rendering, Telegram delivery, checksum verification, and a Notion
  audit that confirmed no submission occurred.
- Telegram may reject acknowledgement of an old button click. The callback UI
  acknowledgement is therefore best-effort after the durable Notion transition;
  a stale acknowledgement cannot block the workflow or leave its offset stuck.
- `Request changes` transitions `cv_drafted -> revision_requested`; feedback is
  saved privately, and `cv revise` creates a new `vN` archive directory. The
  former draft ID cannot approve the newer CV.
- Link labels are set per URL in the private `cv_style.yaml`, which allows
  personal GitHub, organization GitHub, LinkedIn, and project links to remain
  distinct without exposing raw URLs.
- Project evidence includes candidate-maintained `selection_keywords`. Projects
  that do not match the target job are removed before the model request and are
  rejected again during validation if returned. The header keeps only general
  professional profiles; repository links render only with a selected relevant
  project. This prevents a C++ repository from appearing on a Python/agent CV.
- Small cloud exercises are represented in the appropriate technical-skills
  category instead of consuming a standalone Projects entry.
- The private `tailoring_guidance` list in `cv_style.yaml` holds reusable
  precision rules. It can describe how verified technologies should be named,
  but cannot authorize unsupported claims.
- Before a draft exists, an authorized user can send
  `/tailor_<page-id> <instruction>` in Telegram. The private note is scoped to
  the selected tracker and incorporated into the next draft as emphasis-only
  guidance. Its SHA-256 digest is retained in the immutable draft manifest.

## Phase 8: Submission And Tracking

The submitter should be conservative.

Allowed:

- Prepare application material.
- Open browser-assisted flows.
- Fill simple forms after approval where allowed.
- Update Notion after approval.
- Send a reviewed email after a final Telegram email-review approval.

Not allowed:

- Submit without approval.
- Bypass anti-bot systems.
- Misrepresent user data.
- Guess employer contact addresses or numbers.
- Automate a personal WhatsApp account.

Current implementation:

- Two Notion data sources separate testing from real applications. Tracker-aware
  commands default to `test`; production commands must pass
  `--tracker production`. Telegram callback payloads include a compact tracker
  code and reject cross-tracker actions.
- `jobs promote` copies one test row to a fresh production workflow only after a
  verified direct company `Application URL` is supplied. It preserves the
  discovery `Source URL`, recalculates the score, and requires new approvals.
- `notion add-submission-fields` provisions the delivery properties and every
  workflow value used by the status machine. It preserves existing Notion
  status options while adding only missing values.

- Contact discovery accepts only details visibly present in the job description
  and an explicit official company careers/contact URL. It saves a private local
  record before an email review may name a recipient.
- Gmail uses a local OAuth desktop flow with `gmail.send` and `gmail.readonly`;
  its refresh token is held in macOS Keychain. Send is used only for approved
  outreach, and readonly is used for inbox status tracking. Telegram shows the
  recipient, source, exact body, and approved attachment before the delivery
  button becomes available.
- Inbox classification requires recruiting context from the sender or message,
  then applies deterministic status phrases. A standalone commercial word such
  as `offer` is never enough to create an application update. Every inspected
  message, including an ignored one, is checkpointed after a successful scan.
- An expired or revoked Gmail refresh token is a paused integration rather than
  a retrying workflow failure. The watcher sends one actionable Telegram alert
  per 24 hours, completes its scheduled task without retry spam, and clears the
  alert checkpoint after the next successful Gmail request. Job-board scouting
  remains independent and continues while Gmail needs reconnection.
- A Gmail success writes `submitted`, `email`, contact source, Gmail message ID,
  timestamp context, and exact CV draft ID to Notion.
- Portal preparation uses a dedicated visible Playwright profile. It fills only
  clear name/email/phone/location fields and a file upload, then stops for all
  ambiguous questions. CAPTCHA detection transitions to
  `awaiting_human_verification`; Chrome Remote Desktop is human-operated.
- The portal submit path verifies that the exact approved PDF is attached to
  the Resume/CV input in the active browser session. Missing required fields or
  an unverified attachment prevent the submit click.
- When an official careers page uses a public `Apply Now` link to hand off to
  an ATS, portal preparation follows that navigation before field detection.
- Ambiguous form answers are saved only after candidate confirmation, in a
  private per-application record that is bound to its Notion page and tracker.
- A candidate may explicitly save a response as private reusable guidance. It
  remains editable per role and never overrides job-specific candidate input.
- Portal adapters prefer accessible field labels and native file inputs over
  generated DOM IDs, including Wix-hosted forms.
- `apply request-submit` sends a final Telegram review that names the company,
  role, portal host, and exact CV draft. Its private authorization is bound to
  the page, tracker, portal URL, DOCX checksum, and PDF checksum. It has no
  clock expiry, can be consumed once, and becomes invalid when a binding changes.
- `apply resume` may click an unambiguous submit control only after that final
  Telegram approval. If a CAPTCHA appears before or after that click, it keeps
  the exact visible browser session open for the configured remote-verification
  wait window (600 seconds by default), without solving or inspecting the
  challenge. Resuming from `awaiting_human_verification` passes the same wait
  window instead of closing the browser immediately. It records a portal
  submission only when the page visibly confirms it; a click without
  confirmation stays `submission_prepared` and consumes the approval
  conservatively.
- CAPTCHA-solving services, challenge-token replay, browser-fingerprint
  evasion, and undocumented ATS submission endpoints are outside the system's
  design. Official ATS write APIs are used only when the employer or an
  approved integration supplies credentials for that exact purpose. Public job
  board APIs remain suitable for discovery and form-schema inspection.
- `scripts/blackbox_verification_handoff.py` exercises the real headed browser
  against a local fixture whose simulated verification clears after 1.5
  seconds. It proves that the same browser context remains alive and continues
  filling the form without contacting an employer portal.
- When Tailscale is active, the verification wait starts a temporary HTTP
  controller bound only to the Mac's `100.64.0.0/10` Tailscale address. A
  256-bit random path token is sent through Telegram. The controller serves a
  cached screenshot of the active Playwright viewport and queues human touch,
  scroll, key, and typing actions; only the Playwright-owning thread applies
  those actions. It stops when verification clears or the wait expires.
- The handoff refuses wildcard, public, and LAN bindings. The phone must be
  signed into the same tailnet. `system tailscale check` validates the Mac side
  before a live application.
- `scripts/blackbox_tailscale_handoff.py` performs an isolated HTTP black-box
  test over loopback, including the secret URL, cached frame, rejected token,
  and queued touch action. Loopback is available only through the test-only
  constructor switch; production integration still requires a Tailscale IP.
- On confirmed portal success, BackendScout captures a full-page screenshot,
  stores it under the private proof root, hashes it, sends it to Telegram, and
  appends the proof path/checksum to Notion's submission record.
- WhatsApp remains prepared-user-send only. It is never auto-messaged.

### OpenAI-Guided Portal Runtime

The implemented architecture is a deterministic Playwright executor with a
small OpenAI Agents SDK planner used only as a fallback. It is deliberately not
a screenshot-driven computer-use loop or a model-generated browser script.
That keeps routine forms fast, makes each side effect auditable, and limits API
cost to one structured call only when unfamiliar labels remain.

The execution path is:

1. Run existing deterministic selectors and candidate-confirmed answers.
2. If required controls or the CV attachment remain unresolved, capture a
   compact schema containing field IDs, labels, control types, required flags,
   and option labels. Human-verification and submit controls are filtered out.
3. Send only that schema and value-free approved fact descriptors to the fast
   model. Candidate values, screenshots, and the page URL stay on the Mac.
4. Require a structured `GuidedFormPlan` containing field-ID-to-fact-key
   mappings. The planner has no browser tools and cannot click, navigate,
   upload, or submit.
5. Validate the plan locally for known fields, known facts, empty targets,
   control/fact type compatibility, semantic compatibility, and confirmed
   answers for sensitive fields.
6. Resolve approved values locally and execute the accepted mappings. Existing
   CV checksum, Telegram submit authorization, visible confirmation, proof
   screenshot, and Notion audit gates remain unchanged.

The Telegram path is end to end: an inline `Prepare portal` callback queues
preparation, successful preparation sends the final review card, and `Submit
now` queues the authorized resume task. Slow browser and model work stays in
the worker, so the listener acknowledges buttons immediately.

Portal task failures unwrap nested CLI exits before being stored or sent to
Telegram. This keeps the actionable API or validation error instead of the
uninformative numeric exit code. A regression test covers the previously
observed short-ID failure (`/prepare_3da71273`), which now queues nothing and
directs the user to `/submit_status` for reliable buttons.

The task queue deduplicates identical queued or running work and prioritizes a
Telegram-authorized portal submission over unrelated preparation, CV, and
scouting tasks. Portal result messages include the company and role so several
prepared applications cannot be mistaken for one another. Browser inspection
also skips detached frames and tolerates portals rebuilding their iframe tree
during redirects and form rendering.

This follows official OpenAI guidance to use Structured Outputs and keep
domain-specific side effects inside constrained application tools. Tracing is
disabled for this value-sensitive operation, and the planner is limited to one
model turn. `--deterministic-only` remains available for debugging and zero-API
operation.

## Phase 9: Interview Prep

After approval or submission, generate a prep packet:

- Role-specific backend questions.
- System design prompts.
- Missing skills checklist.
- Mini-project ideas if a gap is meaningful.

## Phase 10: Repository Evidence And Inbox Intelligence

The agent should become better at learning from existing proof without making
unsupported claims.

Repository scanner rules:

- Scan only configured local Git roots and public GitHub owners.
- Detect languages, frameworks, dependency files, Docker, CI/CD, test tools, and
  high-level project metadata.
- Produce proposals only. A detected technology is not added to the profile or
  evidence ledger until approved in Telegram.
- Use repository evidence to ask better factual questions, not to inflate CV
  claims.

Inbox watcher rules:

- Use Gmail readonly message bodies to classify obvious application updates;
  never request mailbox write access.
- Mark confirmations, rejections, assessments, interviews, and offers only when
  the signal is clear and the email matches an existing application.
- Send morning digest updates by default; notify immediately only for messages
  that need action, such as an interview or assessment.
- Keep ambiguous recruiting messages as review items instead of changing Notion.
- Checkpoint processed message IDs locally so a 15-minute watcher does not send
  duplicate alerts or append the same audit repeatedly.

Indeed email collector rules:

- Query recent mail from Indeed with the existing Gmail readonly permission.
- Resolve both direct `viewjob` links and opaque `cts.indeed.com` redirects to a
  stable `jk` URL when possible.
- Prefer schema.org `JobPosting` data; when Indeed returns an anti-automation
  response, fall back to the title, company, and location printed in the email.
- Recognize Hebrew and English Israeli locations, merge with the public-board
  candidates, score deterministically, deduplicate, sort, and cap the digest.
- Treat the Indeed URL as discovery evidence. A later application still prefers
  a verified official employer portal.

## Current Next Checkpoint

TODO:

```text
- [x] Add a CV revision path so a candidate can request a corrected draft before final approval.
- [x] Support custom labels for multiple links from the same service, for example
  personal GitHub versus an organization or project GitHub.
- [x] Add reviewed Gmail delivery with a Keychain-held OAuth token.
- [x] Add contact discovery limited to the job post and explicit official company pages.
- [x] Add conservative portal preparation, a human-verification workflow state,
  and a one-time final Telegram submit authorization tied to the exact CV files.
- [x] Add public Greenhouse, Lever, and Ashby collectors for Israel-relevant roles.
- [x] Add Lin-Srael keyword scouting with employer-link preservation and deduplication.
- [x] Add a digest command that chooses the target Telegram chat automatically from config.
- [x] Add test/production Notion tracker separation and explicit promotion.
- [x] Add private global and Telegram per-job CV tailoring guidance.
- [x] Add Linux/HPC and multithreading evidence coverage for the Infinidat role.
- [x] Add read-only repository scanning with proposal reports and Telegram review.
- [x] Add fast Telegram callback acknowledgements, `/status`, `/scout`, and a local task queue.
- [x] Complete the Telegram portal loop so preparation sends the final review
  and `Submit now` queues the approved resume task without a terminal command.
- [x] Add portal proof screenshot capture and Notion audit fields.
- [x] Support candidate-confirmed multi-select checkbox answers in portal forms.
- [x] Add Gmail readonly mailbox classification and a 15-minute launchd watcher template.
- [x] Append mailbox status-change audit records to Notion so each update keeps
  the Gmail message ID, subject, sender, reason, and timestamp.
- [x] Ingest Indeed recommendation emails into the morning production scout.
- [x] Resolve Indeed tracking links, support Hebrew locations, rank/cap the
  digest, and send a run summary even when no new jobs are found.
- [x] Add a mailbox message checkpoint and overlap locks for scheduled jobs.
- [x] Reject commercial inbox false positives and suppress repeated OAuth
  failure notifications while Gmail awaits reauthorization.
- [x] Make every LaunchAgent use the stable project-local uv environment.
- [x] Add a private Application Support runtime deployment for macOS TCC,
  keeping API secrets and state permission-restricted and outside Documents.
- [x] Evaluate OpenAI API submission architectures and document the hybrid
  Agents SDK plus constrained Playwright-tool design.
- [x] Implement and black-box test the OpenAI-guided fallback for unfamiliar
  application forms without changing the existing approval gates.
- [x] Filter CV projects and repository links by explicit job-relevance keywords.
- [ ] Add job-specific interview preparation packets after tailoring approval.
- [x] Keep submission disabled until `approved_to_submit`.
```

## Employer-First Hybrid Delivery (2026-11-04)

This milestone implements architecture baseline 1.1 inside the existing worker,
immutable package and SQLite store. Keep `PORTAL_DELIVERY_MODE=legacy` on the live
installation until a separately approved rollout.

### Resolve And Review Before Writing

`application_routes.py` uses a read-only public browser to follow observed apply
links and embedded forms. It verifies company/title and any job identifier,
preserves discovery/employer/form URLs, and recognizes both Comeet domains.
Unknown forms need exact employer authority and a unique CV application context,
not a domain wildcard. Ambiguity is an owner-input outcome, not a guessed URL.

Migration 5 adds routes, browser observations, proposed answers, budget accounting
and the proof outbox to the existing workflow database. Test migration/restart
behavior with disposable SQLite files, never production copies in fixtures.

The browser worker executes the existing form-check task before tailoring/review.
It records structured questions and evidence-only draft notes. Identity uses
explicit given/family names and separate city/country facts; location is not a
residence city. Required unknowns need owner input before package approval.

Version-1 fingerprint serialization is unchanged. A reserved reviewed answer,
`review.form_schema_sha256`, binds field meaning and consent-link targets.
Only preflight supplies reserved facts. Changed questions, answers, destinations
or PDFs invalidate approval; identical reviews do not regenerate an existing CV.

### Execute A Bounded Loop

`PackagePortalAdapter` delegates to `HybridPortalAdapter` only behind the feature
flag. The adapter observes, fills approved facts, checks retained values, uploads
the exact PDF through controlled code and hashes the browser's actual attachment.
Whole-form scoping excludes unrelated contact widgets. Synthetic regressions
cover Hebrew labels, hidden custom choices, custom uploads, iframes and rerenders.
Duplicate question keys pause before filling; marketing remains off.

The optional OpenAI mapper receives field descriptors and approved fact keys,
not credentials, candidate values, files or executable tools. Code validates its
schema and semantics against a fresh observation. It cannot grant consent,
invent answers, choose a destination, upload or submit. Existing configured
models are retained. See the [official browser integration guide](https://developers.openai.com/api/docs/guides/tools-computer-use-integration).

`browser_budget.py` reserves worst-case cost before paid calls and settles known
token usage. Unknown outcomes retain reservations across restart. Browser caps
are 40 NIS/month, 2 NIS/application, six model calls, 40 actions and 600 active
seconds across resumes; owner wait is excluded. Private pricing JSON at
`PORTAL_AI_PRICES_PATH` maps model names to `input_nis_per_million` and
`output_nis_per_million` decimal strings. Missing rates disable paid fallback,
not deterministic forms. Never invent rates or silently change models.

Hybrid CV generation reserves and settles separate external-model usage under
the same 100 NIS total, without consuming the browser's 40 NIS or per-job limits.
Include the configured CV model's rates in the same pricing file. Unpriced CV
generation pauses explicitly instead of creating repeated failing queue tasks.

### Save Evidence And Recover Safely

The service claims a durable attempt before the external action. The browser
clicks once, waits up to 90 seconds and requires a newly visible, painted receipt
on the verified route. Viewport/full-page PNGs bind URL, timestamp, CV version,
checksums and package fingerprint. An uncertain receipt, disconnect or crash
locks `submission_unknown`; the unresolved-attempt constraint prevents replay.

Success atomically enqueues the Telegram proof notification. Notification and
Notion projection retries never call the employer adapter. Only durable Telegram
acknowledgement lets the browser worker free a completed session; paused forms
remain open. Photo delivery is at-least-once: lost responses can duplicate a
notification, never an employer application.

### Acceptance And Rollout

Run the regular suite plus opt-in intercepted Chromium tests. Five fixtures model
Skyhawk, IBI, Richkid, Raft and SolarEdge/Comeet with synthetic identities. They are
not live test targets; the five submitted real jobs stay locked.

Production hybrid defaults to blocked. Start `PORTAL_HYBRID_ROLLOUT=canary` with
an explicit list of new application IDs and individual exact-package approvals.
General `enabled` rollout requires three distinct hybrid receipts with valid
proofs, not just Notion statuses. Deployment, live canaries, commits and pushes
remain separately approved. This milestone does not alter the morning scout.

Acceptance recorded on 2026-11-05: 1,906 regular tests passed (38 browser opt-ins
skipped there), then all 76 tests in the isolated browser acceptance selection
passed. Ruff, offline lock validation, architecture/evidence validation and
`git diff --check` passed. All five full synthetic service flows reached saved
proof, mocked Telegram acknowledgement and browser cleanup. No real employer,
paid model call, production migration, deployment, commit or push was performed.

### Operations Follow-Up (2026-11-05)

The owner subsequently authorized regular milestone commits and pushes to the
existing repository. The tested recovery and employer-first work is checkpointed
on `codex/employer-first-delivery`; this is not a production deployment or a
blanket employer-submission approval. Check staged content for credentials,
private evidence and CVs before each push; keep docs/ ignored and never force-push.

The Codex heartbeat `backendscout-daily-oversight` is active at 08:30 Asia/Hebron.
It independently checks morning scout/worker outcomes, researches live official
postings, deduplicates additions using the existing production services, and
reports useful new roles or actionable failures to the private owner chat.
Unchanged states remain quiet. The Mac must be awake and Codex running; this is
an extra local supervisor, not cloud hosting or guaranteed always-on coverage.

The current production scout and mailbox outcomes were checked healthy. Hybrid
delivery is still not globally enabled: verified pricing and three newly approved
application packages remain required before general rollout. Do not bypass those
gates merely because a development push succeeded.

### Initial Canary Preparation (2026-11-05)

The owner authorized selecting three new roles and preparing their exact CVs for
review, not submitting them. Official postings were verified, deduplicated and
imported into the production tracker. Three private DOCX/PDF drafts passed the
one-page 90-percent-fill rule and visual checks. Evidence added for BackendScout
describes AI-assisted project work explicitly, not independent SQL expertise.

Real preflight found a legitimate brand/domain mismatch, an "Apply today" label,
a cookie-gated form and an email-only application route. Exact company-scoped
host aliases, controlled final labels and authenticated review deep links now
cover the first two cases. The latter cases remain owner-input/delivery-route
work rather than falsely reported ready portal submissions. Never opt into new
marketing cookies or send an email on the strength of a CV-preparation request.

The private runtime was backed up and upgraded locally to schema 5; worker,
Telegram, web and existing daily schedules were checked healthy afterwards.
Existing models and budget ceilings were preserved. Private conservative pricing
uses a planning valuation rather than pretending to be an actual FX quote; Sol
is valued above its current promotional rates. No approval or submission was
created for the three new applications during preparation.

Completion checkpoint: a tested local hybrid/canary release was activated with
only the three selected private record IDs. All four services and daily schedules
passed the runtime doctor. The live site serves authenticated per-application
deep links; every selected role exposes its final PDF. All six final PDF/Word
files and the unsent email text received Telegram message acknowledgements in
the allowlisted owner chat. The ledger still records zero approvals and zero
delivery attempts for this batch. General rollout remains disabled.

Verification: 1,913 regular tests passed (38 optional-browser skips), 68 tests
passed in the selected isolated browser suite, and authenticated deep-link
pairing/unknown-record checks passed after the last UI change. Private pricing,
source captures, CVs, email drafts and evidence changes remain ignored by Git.

### Query-Based Vacancy Identity (9 November 2026)

Some boards identify vacancies by query parameters rather than distinct paths.
The portal executor now compares explicit vacancy ID parameters before allowing
same-job navigation. Missing, changed, blank and multiple IDs fail closed;
unrelated tracking parameters do not change identity. Existing path-bound ATS
checks remain in place. This is a prerequisite guard, not an enabled AllJobs
delivery adapter. The authenticated UI review remains private under `docs/`.

Verification: 1,938 tests passed, 38 optional tests skipped; the focused portal
and route suites passed 122 tests with six optional browser skips. No live
application, account change or runtime deployment was part of this milestone.

### SecretHunter Discovery and Optional Software QA Targets

Keep third-party account authentication separate from discovery. The initial
SecretHunter connector consumes recommendation emails through the existing Gmail
readonly scope. Enable it in private scouting preferences; the daily scout then
uses one combined bounded mailbox query for Indeed and SecretHunter. Only exact
known sender domains and observed selected-job URLs are accepted. Explicit title,
company and description evidence are required, and unsupported layouts are
reported instead of silently being treated as parsed jobs. This initial parser
has synthetic-template coverage; an actual recommendation email remains a live
compatibility gate. The signed-in browser session is not copied into the worker,
and paid Auto-Apply is never activated by this connector.

Software QA relevance is opt-in through candidate target roles. Tests keep the
backend-only default conservative, preserve avoid-list precedence and prevent a
pharmaceutical QA title from becoming a software match. Skills and tenure still
need real evidence; a numeric match is not proof of professional QA employment.
Live observed vacancies are imported privately with source provenance and clear
experience caveats, using existing tracker services, never as application tests.

November 9 verification: 1,989 tests passed, 38 optional tests skipped; Ruff passed.
An actual bounded Gmail read returned one welcome message and no job alerts, so
live job-alert compatibility is still pending rather than claimed as verified.
The read-only UI scan found four new QA leads; existing SQLite/Notion services
stored them and Telegram acknowledged all four digest messages. No CV upload,
employer contact, premium activation or application submission occurred.

### Lever Attachment Review Regression

Lever replaces the CV upload prompt with the attached filename in both the
visible upload link and its enclosing file-field label. The delivery guard
recognizes only that display change on the known Lever widget after verifying
the exact PDF checksum. Form destinations, consent links and question text
still invalidate a review when they change. Reproduce the issue with a dummy
PDF and synthetic browser routes before retrying any approved application.

Live Telegram testing caught a separate usability issue: help fell through to
the AI assistant queue. Route `/help` and `/start` to a static command menu so
onboarding works without Notion, an available worker, or a paid model call.
Pairing keeps the code in a separate message and uses Telegram's native copy
button to avoid mistyping lookalike characters. The code is still single-use,
expires after five minutes, and is never embedded in a URL.

For browser acceptance, suspend periodic polling, complete a queued form
inspection on the test database, and return focus to the workspace. The
selected detail must update without a navigation, while focused text fields
defer refresh. The streamlined view must not bind handlers to the legacy
details editor, and refresh errors must not leave the mutation lock set.
Run the same isolated workspace flow in WebKit with
`uv run playwright install webkit` and
`BACKENDSCOUT_BROWSER_SMOKE=1 BACKENDSCOUT_WEBKIT_SMOKE=1 uv run pytest tests/test_web_browser.py`.
This supplements, but does not replace, the live Safari check over Tailscale.

Before enabling Job Space, run `backend-scout collect job-space-connect`.
The setup command uses the same parser as the collector and only saves the
credential after validating the result envelope. An empty search proves access
but does not exercise record mapping; require a nonempty live result before
claiming end-to-end compatibility.

An unchanged successful preflight can leave an already approved package back
in review. Show an explicit resume action for that exact fingerprint. Do not
queue work during inspection, require a new review if the package changed, and
never resume a sending, confirmed, or uncertain attempt.

For live notification acceptance, create a plainly synthetic receipt on an
isolated test database and dispatch only its photo to the configured private
owner chat. Verify the Telegram acknowledgement and that a second dispatch
does not claim the record. This does not exercise a real employer submission.
