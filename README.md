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
- Is designed to generate truthful, job-specific CV drafts and interview-prep
  material in later stages.

## Workflow

```text
job found -> scored -> digest sent -> approved to tailor -> CV drafted
-> approved to submit -> reviewed delivery route -> submitted
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
- Conservative browser preparation for approved portal applications. CAPTCHA
  and other human checks remain human-controlled.

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
- Pytest and Ruff for quality checks

## Getting Started

```bash
brew install uv
uv sync --extra dev --no-editable --link-mode copy
cp .env.example .env
cp config/candidate_profile.example.yaml config/candidate_profile.yaml
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

For a model-backed draft, configure `OPENAI_API_KEY` and a private
`CV_ARCHIVE_ROOT` in `.env`, then run:

```bash
uv run --no-editable backend-scout cv draft NOTION_PAGE_ID --chat-id TELEGRAM_CHAT_ID
```

The command writes `cv_draft.docx`, `cv_draft.pdf`, and a private manifest under
the configured archive. The PDF message includes an approval button tied to that
exact draft checksum; only that button can advance the job to `approved_to_submit`.

Use `Request changes` on that Telegram review, then send the feedback in this
exact form and create the next immutable version:

```text
/revise_NOTION_PAGE_ID Make the summary more concise and emphasize API work.
```

```bash
uv run --no-editable backend-scout cv revise NOTION_PAGE_ID --chat-id TELEGRAM_CHAT_ID
```

## Delivery And Portal Workflow

Add these optional Notion properties before enabling delivery: `Submission Channel`
(Select), `Contact Source` (Text), and `Submission Record` (Text). The full setup
is in [REQUIREMENTS.md](REQUIREMENTS.md). BackendScout can add those exact
columns safely to the configured data source:

```bash
uv run --no-editable backend-scout notion add-submission-fields
```

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

```bash
uv run --no-editable backend-scout apply prepare NOTION_PAGE_ID
```

CAPTCHAs are never bypassed. Complete them yourself using Chrome Remote Desktop,
then resume the tracked workflow. The agent clicks a clear submit control only
for an approved application and records `submitted` only after detecting a
confirmation page:

```bash
uv run --no-editable backend-scout apply resume NOTION_PAGE_ID
```

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
