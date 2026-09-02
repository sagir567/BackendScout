# BackendScout Requirements

This file is the install/run checklist for BackendScout. Keep it updated when
we add tools, services, APIs, packages, or system dependencies.

## Current Runtime Requirements

| Requirement | Status | Notes |
| --- | --- | --- |
| macOS | Required | Supported local development environment. |
| Python | Required | Python 3.12, pinned by `.python-version`. |
| uv | Required | Official Python/project environment manager. |
| Notion account | Required | Used as the human-facing application tracker. |
| Notion internal integration | Required | Must have access to the `Applications` data source. |
| PyYAML | Required package | Used for candidate profile and manual job import files. |
| OpenAI API key | Required for model-backed CV drafting | Used only after a job has been approved for tailoring. |
| Telegram bot token | Required for approval loop | Used for the Telegram digest and approval commands. |
| Telegram allowed user IDs | Required for approval loop | Comma-separated numeric Telegram user IDs allowed to approve actions. |
| Google Cloud OAuth desktop client | Optional for email delivery | Used only with the minimum `gmail.send` permission. |
| macOS Keychain | Optional for email delivery | Stores the Gmail refresh token locally, outside `.env` and Git. |
| Playwright browser binary | Optional for portal preparation | Installed locally with `uv run --no-editable playwright install chromium`. |
| Chrome Remote Desktop | Optional for remote CAPTCHA handoff | Human-operated through the user's Google account only. |

## Python Package Requirements

The canonical Python dependency list lives in `pyproject.toml`.

Current runtime packages include Pydantic, Pydantic Settings, Typer, Rich,
HTTPX, Beautiful Soup, python-dotenv, PyYAML, the OpenAI Python SDK,
python-docx, Google API client libraries, Keyring, and Playwright. LibreOffice
is also required locally when generating PDF copies of CV drafts.

The official install command is:

```bash
uv sync --extra dev --no-editable --link-mode copy
```

Because this project intentionally uses a non-editable install, run the
following after pulling source changes to rebuild the local package copy:

```bash
uv sync --extra dev --no-editable --reinstall-package backend-scout
```

For pip-compatible fallback installs, use:

```bash
python -m pip install -r requirements.txt
```

The `requirements.txt` file intentionally points back to `pyproject.toml` so we
do not maintain two separate dependency lists.

## Environment Variables

Create `.env` from `.env.example`.

Current required values:

```bash
OPENAI_API_KEY=TODO
NOTION_API_KEY=TODO
NOTION_API_VERSION=2026-03-11
NOTION_APPLICATIONS_DATA_SOURCE_ID=TODO
TELEGRAM_BOT_TOKEN=TODO
TELEGRAM_ALLOWED_USER_IDS=TODO_COMMA_SEPARATED_NUMERIC_IDS
CV_ARCHIVE_ROOT=TODO_ABSOLUTE_PATH_TO_PRIVATE_CV_ARCHIVE
GMAIL_OAUTH_CLIENT_SECRET_PATH=TODO_ABSOLUTE_PATH_TO_GOOGLE_OAUTH_CLIENT_SECRET_JSON
BROWSER_PROFILE_ROOT=TODO_ABSOLUTE_PATH_TO_PRIVATE_BROWSER_PROFILE
```

`OPENAI_API_KEY` is required only for the model-backed CV drafting command. The
candidate evidence ledger and generated draft files remain local; the command
sends only the approved job description and structured career evidence to the
OpenAI Responses API.

`config/cv_style.yaml` is also required for CV drafting. It is a private
presentation contract; start from `config/cv_style.example.yaml`. The contract
keeps raw URLs out of visible CV text and controls presentation choices such as
whether a headline appears below the name.

Do not commit `.env`.

## Submission Tracker Setup

The existing Applications data source remains compatible with importing,
scoring, Telegram review, and CV drafting. Before enabling an actual delivery
route, add these three properties to the Applications data source:

| Property | Type | Suggested options / purpose |
| --- | --- | --- |
| Submission Channel | Select | `email`, `portal`, `whatsapp` |
| Contact Source | Text | `job_post` or `official_site` plus the reviewed source context |
| Submission Record | Text | Message ID or confirmed portal/WhatsApp audit detail and CV draft ID |

The agent checks for these properties immediately before sending Gmail. It will
not send if the audit fields are absent.

To create the missing fields on the configured Notion data source, run:

```bash
uv run --no-editable backend-scout notion add-submission-fields
```

The command is additive: it creates only missing fields and refuses to replace
an existing field with the wrong type.

## Gmail Setup

1. In Google Cloud Console, create or select a project and enable the Gmail API.
2. Configure the OAuth consent screen for your own Google account.
3. Create an OAuth **Desktop app** client and download its JSON file to a private
   local directory outside this repository.
4. Put its absolute path in `GMAIL_OAUTH_CLIENT_SECRET_PATH` in `.env`.
5. Run `uv run --no-editable backend-scout gmail connect` and complete the local
   Google consent screen.
6. Confirm with `uv run --no-editable backend-scout gmail check`.

The integration requests only `https://www.googleapis.com/auth/gmail.send`.
The OAuth refresh token is stored in macOS Keychain under BackendScout and is
not written to `.env`, this repository, or Notion.

## Playwright And Remote Handoff Setup

Install the isolated Chromium binary once:

```bash
uv run --no-editable playwright install chromium
```

Set `BROWSER_PROFILE_ROOT` to a private local directory. This keeps site login
sessions separate from the normal browser profile.

For remote human verification, install and configure Chrome Remote Desktop under
your own Google account using the [official Chrome Remote Desktop guide](https://support.google.com/chrome/answer/1649523).
When an application requires a CAPTCHA, the agent stops; complete it yourself in
the prepared browser through the remote desktop, then use `apply resume`. That
command can submit only an already-approved form and records success only after
the portal displays a submission confirmation.

WhatsApp is deliberately a handoff only. The system may show a verified public
number and prepare a message, but it never operates a personal WhatsApp account
or records a WhatsApp submission until the user confirms it.

## Official Setup: uv

```bash
brew install uv
uv sync --extra dev --no-editable --link-mode copy
uv run --no-editable backend-scout --help
uv run --no-editable backend-scout notion check
uv run --no-editable backend-scout profile check
uv run --no-editable backend-scout jobs validate examples/manual_job.example.yaml
uv run --no-editable backend-scout telegram check
uv run --no-editable backend-scout telegram peek-updates
uv run --no-editable backend-scout cv --help
uv run --no-editable backend-scout cv evidence-check
```

Pros:

- Can install/manage Python 3.12 when needed.
- Fast dependency installation.
- Creates and manages `.venv`.
- Produces `uv.lock` for reproducible runs.
- `uv run ...` gives a consistent command without manual activation.

Tradeoffs:

- One more tool to learn.
- We should commit `uv.lock` once generated.

## macOS `.venv` Hidden-Flag Note

On this Mac, the first `.venv` created by `uv` inherited the macOS `hidden`
file flag. Python 3.12 skips hidden `.pth` files, which broke editable imports
with `ModuleNotFoundError: No module named 'backend_scout'`.

The official local setup now uses uv's `--no-editable --link-mode copy` options
so the project is installed as a normal wheel inside `.venv`, avoiding the
fragile editable marker and reducing hidden/dataless metadata inherited from
uv's cache.

If that happens, run:

```bash
chflags -R nohidden .venv
uv sync --extra dev --no-editable --link-mode copy
```

If `.venv` under `Documents` still causes slow or stuck imports on this Mac,
move the actual virtualenv outside the iCloud-backed folder and keep `.venv` as
a symlink:

```bash
mv .venv .venv.documents-backup-2026-08-31
uv venv /private/tmp/backendscout-venv
ln -s /private/tmp/backendscout-venv .venv
uv sync --extra dev --no-editable --link-mode copy
```

After that, these commands should work:

```bash
uv run --no-editable backend-scout --help
uv run --no-editable pytest
uv run --no-editable backend-scout notion check
uv run --no-editable backend-scout profile check
uv run --no-editable backend-scout jobs validate examples/manual_job.example.yaml
```

## Fallback Setup: Homebrew Python + venv/pip

Use this only if `uv` is unavailable.

```bash
brew install python@3.12
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
backend-scout --help
backend-scout notion check
backend-scout profile check
backend-scout jobs validate examples/manual_job.example.yaml
```

Pros:

- Familiar Python workflow.
- Uses system-visible Python installed by Homebrew.

Tradeoffs:

- You need to manage `.venv` activation.
- Reproducibility is weaker than the `uv.lock` workflow.

## Current Verification Commands

This syntax check can run with any compatible Python runtime before the full
environment is installed:

```bash
python -m compileall src tests

After dependencies are installed, prefer:

```bash
uv run --no-editable ruff check
uv run --no-editable pytest
uv lock --check
uv run --no-editable backend-scout profile check
uv run --no-editable backend-scout jobs validate examples/manual_job.example.yaml
uv run --no-editable backend-scout jobs score examples/manual_job.example.yaml --profile-path config/candidate_profile.yaml
uv run --no-editable backend-scout jobs import data/raw/company-role.yaml --profile-path config/candidate_profile.yaml --write-notion
uv run --no-editable backend-scout notion check
uv run --no-editable backend-scout telegram check
uv run --no-editable backend-scout telegram peek-updates
```

Expected current import behavior:

- Preview-only unless `--write-notion` is passed.
- Recomputes `Match Score` and `Match Reason` from the local profile before syncing.
- Deduplicates repeated copies of the same job inside one import file.
- Updates an existing Notion row for the same job instead of creating duplicates.
- Preserves existing approval status on updates.

Expected current Telegram behavior:

- `telegram check` verifies the bot token and prints the configured allowed IDs.
- `telegram peek-updates` helps discover the numeric Telegram user ID before approvals are enabled.
- `telegram send-digest --chat-id <id>` sends review messages for jobs in a chosen status.
- Each digest message includes inline approval buttons.
- `telegram poll-once` processes new approval clicks exactly once using a local offset file.

Expected current CV drafting behavior:

- `config/career_evidence.yaml` is private and required for CV drafting.
- `cv draft <notion-page-id> --dry-run` validates an evidence-only preview without
  API calls or artifact writes.
- A model-backed draft requires `OPENAI_API_KEY`, LibreOffice, and a private
  `CV_ARCHIVE_ROOT` outside the repository.
- The resulting DOCX and PDF are paired with a SHA-256 manifest before Telegram
  can offer final CV approval.
- Telegram callback acknowledgements are best-effort. If Telegram has expired a
  button acknowledgement, the already-valid Notion transition still completes
  and the callback offset is recorded safely.

## Local-Only Data Files

These files are intentionally ignored:

- `config/candidate_profile.yaml`: real candidate profile, salary floor, and preferences.
- `config/cv_style.yaml`: personal CV presentation rules.
- `data/raw/`: real pasted job descriptions or manual job import files.
- `.env`: local API tokens and service IDs.
