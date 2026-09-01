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
-> approved to submit -> submitted
```

Applications cannot be submitted until the relevant role and the exact CV
draft have both been approved.

## Current Capabilities

- YAML candidate-profile and manual-job validation.
- Transparent matching with a 100-point score and a written breakdown.
- Stable Notion imports that avoid duplicate job records.
- Telegram digests with `Approve tailoring` and `Close` buttons.
- Authorized Telegram callback processing and Notion status updates.

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

## Project Guide

[SYSTEM_BUILD_GUIDE.md](SYSTEM_BUILD_GUIDE.md) documents the architecture,
design decisions, and implementation roadmap. [REQUIREMENTS.md](REQUIREMENTS.md)
lists runtime requirements and configuration details.
