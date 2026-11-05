# BackendScout

BackendScout is a local-first, human-approved job-search agent for backend
engineering roles.

I am building it for two reasons: to make my job search more systematic, and
to gain hands-on experience designing reliable AI-agent workflows with real
tools, state, approvals, and external integrations.

The production runtime is migrating to DBOS workflows backed by local SQLite.
SQLite owns operational execution state; Notion remains the dashboard I review
and edit. `ARCHITECTURE_RESEARCH.md` explains the findings and migration design.

The implementation baseline is [ARCHITECTURE.md](ARCHITECTURE.md). I track both
the personal service and the separate commercial skill in
[EXECUTION_PLAN.yaml](EXECUTION_PLAN.yaml), with dependencies, acceptance criteria
and evidence. The architecture checksum detects accidental design drift.

```sh
uv run --no-editable python scripts/project_status.py --check-evidence
```

This reports dependency-ready development work and owner/external gates. It is
a read-only development tool; production scheduling remains in DBOS.

The private web workspace now connects immutable application packages to shared
services and durable workers. A package binds the job, tracker, official route,
CV checksum and reviewed answers. Existing Telegram applications retain their
legacy workflow until I explicitly take them over in web review; importing a
Notion status never transfers approval.

The cloud foundation now includes a transactional SQLite application ledger,
an opt-in private-file Gmail credential backend for Linux, and a non-root Docker
image with separate worker lanes. See [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md)
for isolated checks and the future host runbook. Existing local automation stays
on the Mac until the cloud cutover. Linux acceptance, budget enforcement and the
production cloud cutover remain separate milestones.

## Recovery And Next Release

I am improving reliability before moving the always-on work off my laptop.
The recovery slice adds outcome-based health checks, missed-morning recovery,
selection of unseen jobs before the digest limit, explicit blocked tasks, full
mailbox tracker pagination, and dependency validation before release activation.

The legacy Telegram path still uses multiple approval steps. The local mobile
workspace uses SQLite authority and exact-package approval; a production Linux
deployment is still planned, **not an implemented capability**.
See [the delivery plan](RECOVERY_AND_CLOUD_PLAN.md) and
[the Dots assessment](DOTS_ASSESSMENT.md).

Check actual progress independently of whether a process is running:

```sh
uv run --no-editable backend-scout system runtime health
uv run --no-editable backend-scout gmail check
```

The first command reads only SQLite and returns a nonzero exit code for stale
or blocked production workflows. It does not prove external API authorization;
the second command checks Gmail separately. Expired Gmail authorization needs
`backend-scout gmail connect`; public job-board scouting can continue meanwhile.

## What It Does

- Imports and normalizes job opportunities.
- Scores jobs with deterministic, explainable matching rules.
- Uses Notion as the human-facing application dashboard.
- Sends Telegram job digests with approval controls.
- Keeps every application action behind explicit approval gates.
- Generates truthful, job-specific CV drafts after approval and keeps exact
  artifacts versioned for review.

## Private Mobile Workspace

The responsive interface has Today, Agent Apply, Needs My Input, Applications and Health
views, required experience, private PDF review, revision feedback and explicit
answers to application questions. Missing experience is shown as unknown, not
junior. Owner access is paired through the private Telegram `/web` command.

Agent Apply groups active roles with a recognized official ATS destination and
shows the missing prerequisites separately from the professional match score.
It is not a guarantee of unattended submission. A host match or a high match
score does not make a role ready, and historical or uncertain attempts are
excluded. Complete browser preflight and unified email delivery remain in progress.

A known missing or mismatched application link is excluded from Agent Apply and
blocks approval, resume and the delivery worker, while the existing PDF stays
available. Greenhouse availability is checked in the worker before paid drafting
and before delivery. A public 404 means the saved route is unavailable, not proof
that every version of the vacancy is closed. Transient failures and unsupported
schemas do not imply closure. `Update application link` retains the CV and
requires approval of the changed destination.

For Greenhouse roles, `Check form` queues a bounded public API read of published
questions. No employer API key or candidate details are used, and no CV is
uploaded. The result appears in the job detail view. Public schema observation
does not prove all hosted-form requirements are covered. The equivalent CLI is:

```sh
uv run --no-editable backend-scout apply inspect-form <page-id> --tracker production
```

Scouting still requires the hosting Mac to be on. After wake, the watchdog
recovers the current day's missing scout. An exhausted DNS/connectivity failure
gets one additional daily recovery after a thirty-minute cooldown, never a
replay of old digests or application delivery.

```sh
uv sync --extra dev --no-editable
uv run --no-editable backend-scout applications sync --tracker production
uv run --no-editable backend-scout web serve --tracker production
```

Open [the local workspace](http://127.0.0.1:8765) on the Mac. `web pair` sends a
single-use five-minute code to the configured owner chat; it never prints it.
The loopback address is **not** a phone-accessible URL. Phone access needs a
separately configured private HTTPS proxy, such as the existing Tailscale setup;
the app is not published or exposed to the LAN by these commands.

For private phone access, connect the Mac and phone to the same personal
Tailscale network, then check `tailscale serve status` before changing routes.
The intended proxy is `tailscale serve --bg --https=443 http://127.0.0.1:8765`.
If Tailscale returns an enablement link, the owner must sign in and approve
Serve/HTTPS; leave Funnel disabled. After the route is active, set the private
HTTPS origin and `WEB_COOKIE_SECURE=true`, restart the web service, and obtain a
new pairing code through `/web`. Device connectivity alone does not enable the
web proxy. HTTPS certificate names appear in public certificate-transparency
logs; the application itself remains restricted to the tailnet and owner session.
After switching origins, open the private HTTPS address on both the Mac and
phone and pair that browser again. The old loopback address intentionally
cannot perform mutations with its old Origin. Keep Tailscale connected on both
devices and the Mac awake while it hosts the app. To stop only this private
HTTPS proxy, use `tailscale serve --https=443 off`; restoring local-only use also
requires restoring the loopback origin and cookie configuration before reload.

Sessions use hashed opaque tokens, HttpOnly SameSite cookies, CSRF and strict
origin checks. The app has no public signup, analytics, CDN or browser storage
of credentials. The schedule-enabled worker refreshes job snapshots every five
minutes; unavailable Notion access does not remove local reviews.

### Reviewing A Job

I open a role from Today or Applications and check its required experience.
The CV/application controls are near the top of its detail view:

1. `View CV` opens an existing verified PDF; it does not require a complete
   application route. `Create CV` queues a tailored draft when one is needed.
2. `Request changes` opens feedback for a new immutable CV version.
3. `Approve & apply` reviews and authorizes the exact current PDF, destination
   and answers. It remains disabled when a material requirement is missing.
4. `Resume delivery` is available for an unchanged approved package paused before
   sending. A confirmed or uncertain delivery is never replayed automatically.

Creating a CV first fetches missing posting details in the background. `Fetch
job details` only fetches source data and does not request a paid model call.
The worker tries exact public ATS endpoints before a supported public posting
page, verifies job identity, and recalculates matching from retrieved facts.
Source access failures, closed roles and login gates are explicit outcomes,
not invented descriptions. Manual editing is a collapsed last-resort fallback.
An unavailable official submission link does not prevent CV creation or viewing,
but does prevent applying. Active work refreshes in the interface automatically;
editing a field pauses that refresh to avoid interrupting input.

Telegram `/web` and `web pair` send the workspace instructions first and the
single-use code alone in a second message, so copying the code needs no cleanup.

`WEB_ALLOW_DELIVERY=false` is the safe installation default. With it enabled,
`Approve & apply` authorizes the exact displayed package and queues a supported
portal adapter. New material answers or changed CV bytes require a fresh review.
An unchanged package paused before sending can resume without another identical
approval. A submitted or uncertain attempt cannot be blindly replayed. Browser
windows belong to the continuous browser worker, not an individual task, and
remain open while input is needed. Worker restart or user closure may still
require reopening the form; this is not universal unattended ATS support.

The web service is opt-in on macOS:

```sh
uv run --no-editable backend-scout system launchd install web --load
```

Use the deployed runtime as the service project root after `system runtime
deploy --load`. This does not replace the existing morning or mailbox schedules.
From the canonical deployed source checkout, service installation resolves to
the private runtime automatically rather than running from Documents.

The new package adapter supports verified Ashby forms alongside the constrained
ATS registry. It checks the actual uploaded bytes in the active form, retains
the browser on missing input, and requires a new visible receipt plus a painted
PNG before recording success. Workday/custom dropdowns, sign-in and unsupported
forms may still require input; this is not a universal automatic-apply claim.
Local reviews continue during a Notion outage. A leased, retrying outbox updates
existing tracker pages without replaying the application; durable notification
delivery is still a remaining migration task.

## Workflow

```text
job found -> scored -> digest sent -> approved to tailor -> CV drafted
-> approved to submit -> portal prepared -> final Telegram submit approval -> submitted
```

Applications cannot be submitted until the relevant role and the exact CV
draft have both been approved.

## Current Capabilities

- YAML candidate-profile and manual-job validation.
- Transparent matching with a 100-point score and a written breakdown.
- Stable Notion imports that avoid duplicate job records.
- Telegram digests with `Approve tailoring` and `Close` buttons.
- Authorized Telegram callback processing and Notion status updates.
- Source-cited CV drafts in DOCX and PDF after tailoring approval, delivered to
  Telegram with an exact-file approval control.
- A private CV style contract that enforces concise presentation, labeled
  hyperlinks, and no visible raw URLs.
- A repeatable live acceptance-test path using a fictional job, isolated to the
  Notion test table and never capable of submitting an application.
- Versioned CV revisions: a change request creates a new immutable `v2`, `v3`,
  and so on, and invalidates older approval buttons.
- Reviewed Gmail delivery: Telegram shows the exact recipient, source, subject,
  body, and approved attachment before a message can be sent.
- Conservative browser preparation for approved portal applications, followed
  by one-time Telegram authorization for the final submit click.
- Per-application browser checkpoints and verified Greenhouse, Comeet, and
  Lever submit adapters; unsupported portals stop after preparation.
- A low-cost OpenAI-guided fallback for unfamiliar form labels. It sends one
  compact, value-free form schema only after deterministic filling leaves gaps,
  then validates every proposed mapping locally before using approved facts.
- Separate Notion test and production trackers. Production applications require
  an explicit `--tracker production` command or a deliberate test-to-production
  promotion.
- Private global CV guidance plus Telegram per-job tailoring notes, both bound
  to the immutable CV draft manifest.
- Read-only repository scanning that proposes skills/evidence from local Git
  repos and public GitHub repos, without automatically turning them into claims.
- Fast Telegram callback acknowledgements plus `/status` and `/scout` commands
  for a Telegram-first operating loop.
- Durable Telegram update deduplication and per-update failure isolation.
- Persistent `/ask` and free-text Telegram conversations backed by the OpenAI
  Agents SDK; the assistant is advisory and cannot bypass workflow gates.
- Immutable versioned local releases with an atomic `current` pointer and
  shared private state across upgrades.
- A local task queue so long work can be requested from Telegram and processed
  outside the button callback.
- Separate general and single-concurrency browser worker lanes, so a portal
  waiting for attention cannot delay scouting, mailbox updates, or CV work.
- Portal-submission proof screenshots saved locally, hashed, sent to Telegram,
  and recorded in Notion's submission audit.
- Gmail mailbox scanning with `gmail.readonly` to classify confirmations,
  rejections, assessments, interviews, and offers, then append an audit note to
  Notion when a status is updated. A private checkpoint prevents repeated
  Telegram alerts for the same message.
- Public ATS scouting extracts recognizable skill and years-of-experience
  signals from the full job description, then keeps only jobs with meaningful
  target-role relevance in the automatic shortlist.
- Indeed recommendation emails are read through Gmail, reduced to stable
  `jk` job URLs, and merged into the same scored morning shortlist. Hebrew
  Israeli locations and Indeed's opaque tracking redirects are supported.

## Privacy And Safety

This repository contains no real candidate profile, API token, Telegram ID, CV,
or personal archive path. Those values belong only in local ignored files:

- `.env` for service credentials and local paths.
- `config/candidate_profile.yaml` for the real candidate profile.
- `data/raw/` for manually imported job descriptions.

BackendScout only tailors truthful information. It does not invent experience,
bypass platform protections, or submit applications without explicit approval.

## Technology

- Python 3.12 and `uv`
- Pydantic and PyYAML for validated local data
- Notion API for application tracking
- Telegram Bot API via `httpx` for approval messages
- Gmail API with macOS Keychain-held OAuth
- Playwright for visible, conservative portal preparation
- OpenAI Agents SDK for constrained structured form mapping
- Pytest and Ruff for quality checks

## Getting Started

```bash
brew install uv
uv sync --extra dev --no-editable --link-mode copy
cp .env.example .env
cp config/candidate_profile.example.yaml config/candidate_profile.yaml
cp config/repo_sources.example.yaml config/repo_sources.yaml
cp config/scouting_preferences.example.yaml config/scouting_preferences.yaml
```

After pulling source changes, rebuild the copied local package before running
the CLI:

```bash
uv sync --extra dev --no-editable --reinstall-package backend-scout
```

Fill the `TODO` values in the two local files, then run:

```bash
uv run --no-editable backend-scout profile check
uv run --no-editable backend-scout notion check
uv run --no-editable backend-scout telegram check
uv run --no-editable pytest
uv run --no-editable ruff check
```

## Manual Job Workflow

Preview an example job without external writes:

```bash
uv run --no-editable backend-scout jobs validate examples/manual_job.example.yaml
uv run --no-editable backend-scout jobs score examples/manual_job.example.yaml
```

Real job files go in ignored `data/raw/`. Write one to Notion only with an
explicit flag:

```bash
uv run --no-editable backend-scout jobs import data/raw/company-role.yaml --write-notion
```

## Daily Israel-Relevant Collection

Copy `config/target_companies.example.yaml` to the ignored
`config/target_companies.yaml`. It supports public Greenhouse, Lever, and Ashby
company boards plus the Israel-focused Lin-Srael listing feed. Lin-Srael uses
configured search terms and preserves the discovery URL while preferring a
published external employer URL for later application. Start with Israeli
locations and companies; the collector also keeps remote roles when your
candidate profile allows remote work.
It reads each public posting description to infer explicit requirements such as
Python, C++, Linux, Docker, SQL, CI/CD, APIs, cloud tools, and simple `2+ years`
style experience hints. The morning shortlist still filters for meaningful
backend/software role relevance so generic product, admin, or consulting roles
do not crowd the Telegram digest just because they mention a technical keyword.
Recent Indeed recommendation emails are included by default when Gmail is
connected. Their job cards are deduplicated against the public feeds and
existing Notion records.

Lin-Srael searches follow all advertised result pages, with a configurable
`max_pages` safety cap (default 20 per search term). A repeated page or a cap
that leaves results unread produces a collector warning, not a complete-search
claim. Title exclusions match whole words/phrases, including Hebrew; `BI` does
not exclude `Billing` or `Mobile` roles.
Listings without detailed requirements are explicitly preliminary `maybe`
matches, not an `apply` recommendation based only on a promising title.

Preview a collection without writing external state:

```bash
uv run --no-editable backend-scout collect validate
uv run --no-editable backend-scout collect preferences-check
uv run --no-editable backend-scout collect run
```

Tune the ignored `config/scouting_preferences.yaml` file when the digest feels
too broad or too strict. It controls preferred titles, maybe titles, excluded
titles, minimum score, minimum role relevance, and the maximum number of jobs
in one digest. The shortlist is ranked by score before that cap is applied.
Work-mode eligibility is separate: set `work_preferences.onsite: true` in the
private candidate profile to include office-only roles. Telegram cards state
`Work mode` explicitly; allowing onsite work does not reset any approvals.
Every job-review card also states `Experience required`, preserving the
`Years Experience` value from Notion. Missing source information is labeled
as needing verification, never as zero experience or a junior opportunity.

After reviewing the preview, sync new jobs to the default test tracker:

```bash
uv run --no-editable backend-scout collect run --write-notion --send-digest
```

For the real morning flow, production must be explicit:

```bash
uv --cache-dir .uv-cache run --no-editable backend-scout collect run \
  --tracker production --include-indeed-email --write-notion --send-digest
```

For a daily 08:00 production run on macOS, follow the `launchd` setup in
[REQUIREMENTS.md](REQUIREMENTS.md). The scheduled command is the same command
you run manually, and logs remain local under ignored `data/logs/`. The bot
sends a completion summary even when there are no new jobs, so a quiet morning
is distinguishable from a failed scheduler.

The summary separates newly discovered records, previously tracked matches,
pending applications and submitted/closed tracker states. It also reports
source counts and the first exclusion reason for filtered jobs. These are
results from configured feeds, not a claim to cover the entire job market.
Previously delivered jobs are not considered submitted and retain their exact
CV approvals. To recover missed job-review messages without resetting states:

```bash
uv run --no-editable backend-scout collect run --tracker production \
  --write-notion --send-digest --resend-pending
```

New discoveries get priority within the digest limit; remaining slots may
resend jobs still awaiting tailoring approval. Approved or drafted CVs are not
reset or automatically resubmitted.

On macOS, background LaunchAgents cannot read projects inside the protected
`Documents` folder even when they run as the same user. Deploy the private
runtime first; it copies code and private configuration to a mode-`0700`
directory under `~/Library/Application Support`, rewrites runtime-only artifact
paths, preserves operational state across redeployments, and loads all agents:

```bash
uv --cache-dir .uv-cache run --no-editable backend-scout system runtime deploy --load
uv --cache-dir .uv-cache run --no-editable backend-scout system runtime status
uv --cache-dir .uv-cache run --no-editable backend-scout system runtime doctor
uv --cache-dir .uv-cache run --no-editable backend-scout system launchd status
```

Every service runs through the same project-local `.venv` and `uv` cache.
This Git repository remains the development source of truth; rerun the deploy
command after a tested code or private-config change.

Once a checkout is deployed, commands run from that checkout adopt the same
canonical private archive, browser profile, proof directory, and SQLite database
as the background agents. `runtime doctor` checks this path contract and confirms
that the Telegram, task, and browser workers are installed and loaded.

## Repository Evidence Scanner

The scanner reads only the repositories configured in the ignored
`config/repo_sources.yaml` file. It detects languages, dependency files,
frameworks, tests, Docker, CI/CD, and public GitHub metadata. The output is a
proposal report, not an automatic update to my profile or CV ledger.

```bash
uv run --no-editable backend-scout repos validate
uv run --no-editable backend-scout repos scan --send-telegram --tracker production
```

After I approve a proposed skill or evidence bullet, it can be copied into the
private `config/candidate_profile.yaml` or `config/career_evidence.yaml`.

## Telegram Workflow

After configuring the bot token and authorized Telegram user IDs in `.env`:

```bash
uv run --no-editable backend-scout telegram peek-updates
uv run --no-editable backend-scout telegram send-digest --chat-id TELEGRAM_CHAT_ID
uv run --no-editable backend-scout telegram poll-once
```

`send-digest` sends jobs whose current Notion status is `found` and advances
them to `digest_sent`. `poll-once` processes button presses and records the
valid next status in Notion.

If an automation window closes, send `/submit_status` and use **Open in
browser**, or send `/open_FULL_NOTION_PAGE_ID`. This reopens the stored employer
entry link in my own browser without enqueuing submission or changing approvals.
Transient sign-in URLs are not reusable application links; the discovery URL
is retained as a fallback. Unsaved form values and file uploads are not restored
by reopening a link. The exact CV artifacts remain in the private archive.

All tracker-aware commands default to `--tracker test`. Use
`--tracker production` only for the real Applications table. Telegram buttons
carry their tracker identity, so a test approval cannot alter a production row.

For continuous production-workflow processing on the Mac, use the dedicated
`launchd` listener described in [REQUIREMENTS.md](REQUIREMENTS.md). It only
processes authorized button actions plus supported commands:

```text
/status
/jobs
/today
/submit_status
/scout
/draft_NOTION_PAGE_ID
/tailor_NOTION_PAGE_ID Emphasize this truthful angle.
/revise_NOTION_PAGE_ID Make the summary tighter.
```

`/jobs` shows open actionable applications, `/today` shows recent tracked jobs,
and `/submit_status` shows jobs ready for portal progress. Ordinary text cannot
authorize delivery or submission. Buttons are acknowledged quickly, and longer
work is queued for a worker command:

```bash
uv run --no-editable backend-scout tasks list
uv run --no-editable backend-scout tasks worker-once
```

With the launchd worker enabled, pressing `Approve tailoring` queues the CV
draft automatically. Approving the exact CV now returns a `Prepare portal`
button that safely carries the full Notion page ID. `/submit_status` resends
`Prepare` buttons for CV-approved jobs and `Resume` buttons for portals that
still need fields or attachments. The typed `/prepare_NOTION_PAGE_ID`
form remains a fallback and rejects incomplete IDs before queueing any work.

Candidate-confirmed portal answers are stored privately per job and tracker.
The answer store supports both single controls and exact multi-select choices,
so forms requiring several checkbox selections can be resumed consistently.

## CV Draft Workflow

Copy `config/career_evidence.example.yaml` to the ignored
`config/career_evidence.yaml`, then fill it with factual career evidence. Each
experience and project bullet has a stable evidence ID used to audit model output.
Also copy `config/cv_style.example.yaml` to the ignored `config/cv_style.yaml`.
This is where personal presentation rules live. The default keeps the headline
under your name disabled, shows technical skills in their own section, and turns
GitHub, LinkedIn, and project URLs into labeled clickable links instead of visible
raw URLs.

Experience headings keep the role title on its own line and the organization
and dates together below it, so long titles cannot collide with dates. AI-assisted
project work retains its ownership qualifiers rather than implying unaided
implementation or professional experience in every tool used.

Each project has explicit `selection_keywords`. Before the OpenAI request,
BackendScout removes projects whose keywords do not appear in the target job.
The CV header keeps only general profiles such as the primary GitHub account and
LinkedIn; a repository link is rendered only inside a project that passed this
relevance check and was selected for the draft. Small cloud practice belongs in
the relevant technical-skills category rather than a standalone project.

For a no-write validation preview after a job reaches `approved_to_tailor`:

```bash
uv run --no-editable backend-scout cv evidence-check
uv run --no-editable backend-scout cv draft NOTION_PAGE_ID --dry-run
```

Before every CV draft, BackendScout also calculates a separate **CV evidence
coverage** score against the job's explicit requirements. This is not the job
match score and does not change it. The target is 90/100: when the evidence is
below that target, drafting pauses and the agent lists the exact factual gaps to
resolve. A gap can be closed only by confirmed experience or a completed project,
never by adding a claim just to raise a score.

```bash
uv run --no-editable backend-scout cv coverage NOTION_PAGE_ID --tracker test
```

For now, answer those clarification questions in the active conversation and
then update the private evidence ledger. The next Telegram workflow extension
will deliver and collect the same questions through the bot.

For a model-backed draft, configure `OPENAI_API_KEY` and a private
`CV_ARCHIVE_ROOT` in `.env`, then run:

```bash
uv run --no-editable backend-scout cv draft NOTION_PAGE_ID --chat-id TELEGRAM_CHAT_ID
```

The command writes `<candidate name> CV.docx`, `<candidate name> CV.pdf`, and a
private manifest under the configured archive. The PDF message includes an approval button tied to that
exact draft checksum; only that button can advance the job to `approved_to_submit`.
Every generated CV is checked against a hard presentation contract: exactly one
page, with visible text reaching at least 90% of the usable page height. A draft
that cannot meet both conditions fails before it is sent for review. When normal
typography scaling would wrap the document to a second page, BackendScout can
increase vertical spacing independently so it fills the page without adding
irrelevant content or changing factual claims. Model-produced skill labels that
do not exactly match verified evidence are removed before final validation.

Add a private, job-specific note before drafting with Telegram:

```text
/tailor_NOTION_PAGE_ID Emphasize the verified PrintIt C#/.NET 8 backend work for this role.
```

The agent acknowledges the note and attaches it to the next draft manifest. It
can influence emphasis only; factual claims still must be supported by the
career evidence ledger. Global reusable rules belong in the private
`tailoring_guidance` list in `config/cv_style.yaml`.

Use `Request changes` on that Telegram review, then send the feedback in this
exact form and create the next immutable version:

```text
/revise_NOTION_PAGE_ID Make the summary more concise and emphasize API work.
```

```bash
uv run --no-editable backend-scout cv revise NOTION_PAGE_ID --chat-id TELEGRAM_CHAT_ID
```

## Delivery And Portal Workflow

Add the `Application URL` field plus the delivery audit properties before
enabling delivery. The full setup is in [REQUIREMENTS.md](REQUIREMENTS.md).
BackendScout adds missing columns safely to the selected tracker:

```bash
uv run --no-editable backend-scout notion add-submission-fields --tracker production
```

The same command also adds any missing lifecycle values to Notion's `Status`
field, including `submission_prepared` and `awaiting_human_verification`.

Discover only contacts printed in the job post or an official company URL you
explicitly provide:

```bash
uv run --no-editable backend-scout contacts discover NOTION_PAGE_ID
uv run --no-editable backend-scout contacts discover NOTION_PAGE_ID --official-url https://company.example/careers
```

After the exact CV is approved, prepare an email review in Telegram. The bot
cannot send until the `Send approved email` button is pressed by an authorized
user:

```bash
uv run --no-editable backend-scout gmail connect
uv run --no-editable backend-scout gmail check
uv run --no-editable backend-scout contacts review-email NOTION_PAGE_ID \
  --recipient hiring@company.example --chat-id TELEGRAM_CHAT_ID
uv run --no-editable backend-scout telegram poll-once
```

`gmail check` performs a real readonly API request, so it detects an expired or
revoked refresh token instead of only checking whether Keychain contains a
token. The recurring inbox watcher requires recruitment context before it
notifies Telegram; generic commercial uses of words such as "offer" are
ignored and checkpointed. If Google rejects the stored authorization, inbox
tracking pauses and sends at most one reconnect notice per 24 hours. Daily
job-board scouting continues while Gmail is paused.

For portal applications, the agent opens a visible persistent browser profile,
fills clear evidence-backed fields, and attaches the approved CV. Deterministic
selectors run first. If required controls remain unresolved, the default guided
mode makes one structured call using `OPENAI_MODEL_FAST`. The model sees field
labels, control types, option labels, and approved fact descriptions, but never
the candidate values, a screenshot, or the page URL. BackendScout resolves the
chosen fact keys locally and rejects mismatched, populated, unsupported, or
sensitive fields without a job-specific confirmed answer.
When an official careers page has an explicit `Apply Now` link to its ATS, the
agent follows that public navigation before filling the form.
Candidate-confirmed form answers are stored privately per job; the agent never
reuses an answer from one application for another role unless I explicitly
approve it as reusable guidance. Portal adapters support accessible labels and
hidden native CV inputs used by common Wix forms; random generated IDs are not
treated as stable selectors.
Before an authorized submit click, the agent verifies that the exact approved
PDF is attached to the portal's Resume/CV control in that same browser session.

```bash
uv run --no-editable backend-scout apply prepare NOTION_PAGE_ID --tracker production
```

Use `--deterministic-only` to disable the optional mapper for a run. Ordinary
forms that the deterministic adapter completes use no model tokens.

From Telegram, press the `Prepare portal` button sent after CV approval. The
listener acknowledges it immediately and the worker performs preparation. A complete form causes the
worker to send the final review card automatically; unresolved fields are sent
back to the same authorized chat.

Portal submissions have higher queue priority than new portal preparations, so
a `Submit now` authorization is processed before unrelated forms.
Repeated presses for the same active task reuse its existing queue record, and
every attention message identifies the company and role it belongs to.

After preparation, request the final Telegram card. Its `Submit now` button
authorizes one browser submit click for the exact current CV and portal URL,
has no clock expiry, and cannot be reused or transferred to another CV revision,
portal URL, job, or tracker:

```bash
uv run --no-editable backend-scout apply request-submit NOTION_PAGE_ID --tracker production
uv run --no-editable backend-scout telegram poll-once --tracker production
```

In the continuous Telegram runtime, pressing `Submit now` creates the one-time
authorization and queues `apply resume` automatically. No terminal command is
needed for that path. The command remains available for recovery and debugging.

If the browser enters `awaiting_human_verification`, the existing handoff keeps
the exact visible browser session open for up to 10 minutes by default. The
session remains open when resuming, so cookies and form state are not discarded.
The application is recorded as `submitted` only after the portal displays a
confirmation page; otherwise a new final approval is required before another
click:

```bash
uv run --no-editable backend-scout apply resume NOTION_PAGE_ID \
  --tracker production --wait-for-human-seconds 600
```

On a visible portal confirmation, BackendScout saves a full-page proof
screenshot under the private `SUBMISSION_PROOF_ROOT`, sends it to Telegram, and
records the screenshot path, checksum, portal URL, and exact CV draft in Notion.

When Tailscale is installed and connected on the Mac, human verification uses
a narrower mobile handoff instead of full remote desktop. BackendScout binds a
temporary controller only to the Mac's private Tailscale address, protects it
with a random URL token, and sends the link through Telegram. The page exposes
only the active application-browser viewport and closes with the verification
wait. Install Tailscale on the phone under the same account, then verify the Mac:

```bash
uv run --no-editable backend-scout system tailscale check
```

The controller supports touch clicks, scrolling, navigation keys, and typing
into the currently focused browser control. All Playwright interaction remains
on the browser's owning thread. BackendScout never exposes the controller on
`0.0.0.0`, a public address, or the local LAN.

For a verified public WhatsApp number, the agent opens a prefilled WhatsApp Web
chat and sends the approved PDF to Telegram for you to attach. It never sends
the WhatsApp message itself; a Telegram confirmation records it only after you
have sent it:

```bash
uv run --no-editable backend-scout contacts whatsapp-handoff NOTION_PAGE_ID \
  --phone 972501234567 --chat-id TELEGRAM_CHAT_ID
```

## Project Guide

### Employer-First Delivery (Local Preview)

A separate Codex oversight heartbeat is scheduled for 08:30 Asia/Hebron. It
checks the 08:00 scout, independently finds relevant official postings,
deduplicates useful additions, and reports new roles or actionable failures to
the private Telegram chat. This local layer requires the Mac awake and Codex
running; it is not a cloud availability guarantee. Employer delivery still
requires exact-package approval.

The first owner-approved preparation batch is now under limited canary review.
Public forms with a company-brand/domain mismatch can use an operator-verified
exact-host alias in ignored `config/employer_domains.private.yaml`; see the
fictional example. A web page or model cannot add aliases or grant submission
approval. Telegram review links open the specific application after owner login.
Email-only roles and new cookie consents remain explicit separate review steps;
three prepared CVs do not count as three successful portal canaries.

On 2026-11-05 the owner opted into local canary preparation. The running release
uses hybrid/canary mode with three explicit private record IDs, not general
enablement. Their PDFs/Word files are reviewable in the private workspace and
were sent only to the owner's Telegram chat. One public form is verified; one
requires new cookie consent, and one employer's route is email-only. No employer
delivery or package approval is implied by this preparation checkpoint.

I am testing a hybrid submission engine behind `PORTAL_DELIVERY_MODE=hybrid`.
The default remains `legacy`, so installing this code does not enable new live
submissions or change the morning scout. No extra dependency or service is needed.

Job boards supply discovery links. A read-only browser preflight follows observed
employer/ATS links, verifies the company and role, and records the destination in
the existing workflow SQLite database. The review package binds the exact PDF,
answers, optional cover note, destination, and a signature of the questions and
consent links. New questions require review, not a new CV by default.

Known fields use deterministic filling. The optional OpenAI fallback maps current
field IDs to approved facts only; it cannot invent answers, upload files, accept
terms, choose a new recipient, or authorize submission. Marketing stays off.
Missing pricing disables paid fallback while known forms remain usable.

After the one submit action, the engine waits up to 90 seconds for a new visible
receipt and saves a viewport proof plus a full-page screenshot. An uncertain
result is locked as `submission_unknown`; it is never automatically resubmitted.
Notion projection and Telegram proof delivery retry independently. A completed
browser closes only after Telegram acknowledges the saved proof; paused forms
remain open.

Local acceptance uses five synthetic employer forms, never the five applications
already submitted. The opt-in browser tests intercept network requests:

```bash
uv run --no-editable pytest
BACKENDSCOUT_BROWSER_SMOKE=1 uv run --no-editable pytest \
  tests/test_hybrid_portal.py tests/test_employer_forms.py \
  tests/test_package_portal.py tests/test_web_browser.py
```

Production starts with `PORTAL_HYBRID_ROLLOUT=canary` and an explicit
`PORTAL_CANARY_APPLICATION_IDS` list. General enablement requires three distinct
new, approved hybrid applications with saved confirmation proof. Existing
submitted applications are never rollout candidates.

[SYSTEM_BUILD_GUIDE.md](SYSTEM_BUILD_GUIDE.md) documents the architecture,
design decisions, and implementation roadmap. [REQUIREMENTS.md](REQUIREMENTS.md)
lists runtime requirements and configuration details.
