# BackendScout

BackendScout is a local-first job-search agent for backend engineering roles.

The project has two goals:

1. Practice building useful AI agents with real tools, state, approvals, and scheduled workflows.
2. Make the job-search process calmer and more systematic: find relevant roles, score fit, request approval, tailor truthful CV versions, track submissions, and prepare for interviews.

## Current Approach

- Core orchestration: Codex + OpenAI Agents SDK.
- Runtime: Python.
- Tracker: Notion-first. The Notion applications data source is the human-facing source of truth.
- Human approval: Telegram first.
- CV archive: existing CV folders stay outside this repo. Submitted CV versions can continue to be stored in company-named folders in `/Users/sagi/Documents/CV`.

## Learning Guide

This repo is also a learning artifact. Keep [SYSTEM_BUILD_GUIDE.md](SYSTEM_BUILD_GUIDE.md)
updated as we build, so another developer can follow the same path and learn how
to create a complex agent system with tools, state, approval gates, and external
integrations.

Keep [REQUIREMENTS.md](REQUIREMENTS.md) updated with everything needed to run
the system.

## Safety Rules

- Never submit an application without explicit approval.
- Never invent skills, employment history, degrees, companies, metrics, or dates.
- Tailor wording and emphasis only from truthful candidate data.
- Do not bypass CAPTCHA, login restrictions, platform Terms, or anti-abuse systems.
- Do not add deliberate mistakes to disguise AI usage.

## Planned MVP

1. Candidate profile and job preferences.
2. Job collector interfaces.
3. Job parser and deduplication.
4. Match scoring.
5. Telegram daily digest.
6. Application tracker.
7. CV tailoring with approval gates.

## Local Setup

Use `uv`. The project pins Python 3.12 with `.python-version`.

```bash
brew install uv
uv sync --extra dev --no-editable --link-mode copy
uv run --no-editable backend-scout --help
uv run --no-editable backend-scout notion check
uv run --no-editable backend-scout profile check
uv run --no-editable backend-scout jobs validate examples/manual_job.example.yaml
```

The `--no-editable --link-mode copy` flags avoid a macOS hidden/dataless file
issue seen on this machine when the project is installed editably under
`Documents/`.

If the CLI still hangs because `.venv` inherits iCloud/File Provider metadata
from `Documents`, rebuild it outside the repo and keep `.venv` as a symlink:

```bash
mv .venv .venv.documents-backup-YYYY-MM-DD
uv venv /private/tmp/backendscout-venv
ln -s /private/tmp/backendscout-venv .venv
uv sync --extra dev --no-editable --link-mode copy
```

## Notion Setup

BackendScout expects a Notion data source for applications. Create a Notion
database/table, connect it to an internal Notion integration, then put the data
source ID and token in `.env`.

Required environment values:

```bash
NOTION_API_KEY=
NOTION_API_VERSION=2026-03-11
NOTION_APPLICATIONS_DATA_SOURCE_ID=
```

The `Applications` data source should contain these properties:

| Property | Type |
| --- | --- |
| Role | Title |
| Company | Text |
| Status | Status |
| Source | Text |
| Source URL | URL |
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

Verify the connection:

```bash
uv run --no-editable backend-scout notion check
```

## Candidate Profile

Real candidate data lives in `config/candidate_profile.yaml`. That file is
local-only and ignored by git.

The committed starter is `config/candidate_profile.example.yaml`. The local
profile should stay truthful and specific because later matching and CV
tailoring will rely on it.

Keep these profile fields current:
- target roles and locations
- salary floor in NIS
- remote/hybrid/onsite preferences
- concrete backend skills
- proof points backed by real work, projects, publications, or public GitHub work

Validate it:

```bash
uv run --no-editable backend-scout profile check
```

## Manual Job Import

Use manual imports before adding scrapers. This lets us test parsing, validation,
Notion writes, and the human workflow with jobs you choose.

Committed example:

```bash
uv run --no-editable backend-scout jobs validate examples/manual_job.example.yaml
uv run --no-editable backend-scout jobs import examples/manual_job.example.yaml
```

Real job import files should live under ignored `data/raw/`.

The import command is preview-only by default. To create Notion rows, use:

```bash
uv run --no-editable backend-scout jobs import data/raw/company-role.yaml --profile-path config/candidate_profile.yaml
uv run --no-editable backend-scout jobs import data/raw/company-role.yaml --profile-path config/candidate_profile.yaml --write-notion
```

`jobs import` now recomputes `Match Score` and `Match Reason` from your local
candidate profile before writing to Notion, so the tracker always reflects the
current scoring rules rather than stale YAML values.

Repeated imports are now stable:
- duplicate jobs inside one YAML file are deduplicated before sync
- an existing Notion row is updated instead of duplicated when the same job is imported again
- existing workflow status is preserved, so a reviewed job does not get reset to `found`

For a detailed shortlist view without writing to Notion:

```bash
uv run --no-editable backend-scout jobs score data/raw/company-role.yaml --profile-path config/candidate_profile.yaml
```

## Telegram Approval Loop

Telegram is now the first approval channel. The current slice uses the raw Bot
API through `httpx`, with two explicit commands:

```bash
uv run --no-editable backend-scout telegram check
uv run --no-editable backend-scout telegram peek-updates
uv run --no-editable backend-scout telegram send-digest --chat-id YOUR_TELEGRAM_USER_ID
uv run --no-editable backend-scout telegram poll-once
```

Current behavior:
- `telegram send-digest` reads jobs from Notion by status, sends one message per job, and moves `found` jobs to `digest_sent`
- each Telegram message includes inline buttons for `Approve tailoring` and `Close`
- `telegram poll-once` reads new callback updates once, applies valid status transitions, and stores the last processed Telegram update ID locally
- only configured Telegram user IDs may trigger approval actions

Use the setup worksheet in [TELEGRAM_SETUP_WORKSHEET.md](TELEGRAM_SETUP_WORKSHEET.md)
to fill the local bot config without guessing.

## Git Policy

- `docs/` is local-only and should not be committed.
- `.env` is local-only and must never be committed.
- Ask Sagi before pushing to any remote.
