# BackendScout

BackendScout is a local-first, human-approved job-search agent for backend
engineering roles.

I am building it for two reasons: to make my job search more systematic, and
to gain hands-on experience designing reliable AI-agent workflows with real
tools, state, approvals, and external integrations.

## What It Does

- Imports and normalizes job opportunities.
- Scores jobs with deterministic, explainable matching rules.
- Uses Notion as the application tracker and source of workflow state.
- Sends Telegram job digests with approval controls.
- Keeps every application action behind explicit approval gates.
- Generates truthful, job-specific CV drafts after approval and keeps exact
  artifacts versioned for review.

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
  by one-time Telegram authorization for the final submit click. CAPTCHA and
  other human checks remain human-controlled.
- Separate Notion test and production trackers. Production applications require
  an explicit `--tracker production` command or a deliberate test-to-production
  promotion.
- Private global CV guidance plus Telegram per-job tailoring notes, both bound
  to the immutable CV draft manifest.
- Read-only repository scanning that proposes skills/evidence from local Git
  repos and public GitHub repos, without automatically turning them into claims.
- Fast Telegram callback acknowledgements plus `/status` and `/scout` commands
  for a Telegram-first operating loop.
- A local task queue so long work can be requested from Telegram and processed
  outside the button callback.
- Portal-submission proof screenshots saved locally, hashed, sent to Telegram,
  and recorded in Notion's submission audit.
- Gmail mailbox scanning with `gmail.readonly` to classify confirmations,
  rejections, assessments, interviews, and offers, then append an audit note to
  Notion when a status is updated.
- Public ATS scouting extracts recognizable skill and years-of-experience
  signals from the full job description, then keeps only jobs with meaningful
  target-role relevance in the automatic shortlist.

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
`config/target_companies.yaml`. It lists only public Greenhouse, Lever, and
Ashby company boards. Start with Israeli locations and companies; the collector
also keeps remote roles when your candidate profile allows remote work.
It reads each public posting description to infer explicit requirements such as
Python, C++, Linux, Docker, SQL, CI/CD, APIs, cloud tools, and simple `2+ years`
style experience hints. The morning shortlist still filters for meaningful
backend/software role relevance so generic product, admin, or consulting roles
do not crowd the Telegram digest just because they mention a technical keyword.

Preview a collection without writing external state:

```bash
uv run --no-editable backend-scout collect validate
uv run --no-editable backend-scout collect preferences-check
uv run --no-editable backend-scout collect run
```

Tune the ignored `config/scouting_preferences.yaml` file when the digest feels
too broad or too strict. It controls preferred titles, maybe titles, excluded
titles, minimum score, and minimum role relevance for automatic scouting only.

After reviewing the preview, sync new jobs to the default test tracker:

```bash
uv run --no-editable backend-scout collect run --write-notion --send-digest
```

For the real morning flow, production must be explicit:

```bash
uv --cache-dir .uv-cache run --no-editable backend-scout collect run \
  --tracker production --write-notion --send-digest
```

For a daily 08:00 production run on macOS, follow the `launchd` setup in
[REQUIREMENTS.md](REQUIREMENTS.md). The scheduled command is the same command
you run manually, and logs remain local under ignored `data/logs/`.

The runtime helpers can be installed from the CLI:

```bash
uv --cache-dir .uv-cache run --no-editable backend-scout system launchd install all
uv --cache-dir .uv-cache run --no-editable backend-scout system launchd status
```

Add `--load --replace` to refresh and start already-installed agents.

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
/prepare_NOTION_PAGE_ID
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
draft automatically. After approving the exact CV, send `/prepare_NOTION_PAGE_ID`
when I want the agent to prepare the browser portal.

## CV Draft Workflow

Copy `config/career_evidence.example.yaml` to the ignored
`config/career_evidence.yaml`, then fill it with factual career evidence. Each
experience and project bullet has a stable evidence ID used to audit model output.
Also copy `config/cv_style.example.yaml` to the ignored `config/cv_style.yaml`.
This is where personal presentation rules live. The default keeps the headline
under your name disabled, shows technical skills in their own section, and turns
GitHub, LinkedIn, and project URLs into labeled clickable links instead of visible
raw URLs.

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

The command writes `cv_draft.docx`, `cv_draft.pdf`, and a private manifest under
the configured archive. The PDF message includes an approval button tied to that
exact draft checksum; only that button can advance the job to `approved_to_submit`.
Every generated CV is checked against a hard presentation contract: exactly one
page, with visible text reaching at least 90% of the usable page height. A draft
that cannot meet both conditions fails before it is sent for review.

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
uv run --no-editable backend-scout contacts review-email NOTION_PAGE_ID \
  --recipient hiring@company.example --chat-id TELEGRAM_CHAT_ID
uv run --no-editable backend-scout telegram poll-once
```

For portal applications, the agent opens a visible persistent browser profile,
fills only clear evidence-backed fields, and attaches the approved CV. It does
not answer legal, demographic, eligibility, or salary questions.
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

After preparation, request the final Telegram card. Its `Submit now` button
authorizes one browser submit click for the exact current CV and portal URL,
expires in 15 minutes, and cannot be reused for another CV revision or tracker:

```bash
uv run --no-editable backend-scout apply request-submit NOTION_PAGE_ID --tracker production
uv run --no-editable backend-scout telegram poll-once --tracker production
```

CAPTCHAs are never bypassed. If one appears before or after the authorized
click, the agent keeps the exact visible browser session open for remote human
verification (10 minutes by default). The session now remains open when
resuming from `awaiting_human_verification`, so cookies, form state, and the
challenge are not discarded immediately. Complete only that check with a
remote-control tool you trust. The agent records `submitted` only after
detecting a confirmation page; otherwise a new final approval is required
before another click:

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

[SYSTEM_BUILD_GUIDE.md](SYSTEM_BUILD_GUIDE.md) documents the architecture,
design decisions, and implementation roadmap. [REQUIREMENTS.md](REQUIREMENTS.md)
lists runtime requirements and configuration details.
