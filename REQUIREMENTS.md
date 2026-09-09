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
| Google Cloud OAuth desktop client | Optional for email and inbox tracking | Uses `gmail.send` for approved outreach and `gmail.readonly` for mailbox status updates. |
| macOS Keychain | Optional for Gmail | Stores the Gmail refresh token locally, outside `.env` and Git. |
| Playwright browser binary | Optional for portal preparation | Installed locally with `uv run --no-editable playwright install chromium`. |
| Chrome Remote Desktop | Optional for remote CAPTCHA handoff | Human-operated through the user's Google account only. |

## Python Package Requirements

The canonical Python dependency list lives in `pyproject.toml`.

Current runtime packages include Pydantic, Pydantic Settings, Typer, Rich,
HTTPX, Beautiful Soup, python-dotenv, PyYAML, the OpenAI Python SDK,
python-docx, pdfplumber, Google API client libraries, Keyring, and Playwright.
LibreOffice is also required locally when generating PDF copies of CV drafts.

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
NOTION_TEST_APPLICATIONS_DATA_SOURCE_ID=TODO
NOTION_PRODUCTION_APPLICATIONS_DATA_SOURCE_ID=TODO
TELEGRAM_BOT_TOKEN=TODO
TELEGRAM_ALLOWED_USER_IDS=TODO_COMMA_SEPARATED_NUMERIC_IDS
CV_ARCHIVE_ROOT=TODO_ABSOLUTE_PATH_TO_PRIVATE_CV_ARCHIVE
GMAIL_OAUTH_CLIENT_SECRET_PATH=TODO_ABSOLUTE_PATH_TO_GOOGLE_OAUTH_CLIENT_SECRET_JSON
BROWSER_PROFILE_ROOT=TODO_ABSOLUTE_PATH_TO_PRIVATE_BROWSER_PROFILE
SUBMISSION_PROOF_ROOT=TODO_ABSOLUTE_PATH_TO_PRIVATE_SUBMISSION_PROOFS
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

## Test And Production Trackers

BackendScout uses two separate Notion data sources. The current test table stays
isolated by default. Every tracker-aware command defaults to `--tracker test`;
use `--tracker production` for an actual application.

1. Create or select the real Applications data source in Notion.
2. Share it with the existing BackendScout integration.
3. Copy its data-source ID into `NOTION_PRODUCTION_APPLICATIONS_DATA_SOURCE_ID`.
4. Keep the existing test-table ID in `NOTION_TEST_APPLICATIONS_DATA_SOURCE_ID`.
5. Validate each table independently:

```bash
uv run --no-editable backend-scout notion check --tracker test
uv run --no-editable backend-scout notion check --tracker production
```

`NOTION_APPLICATIONS_DATA_SOURCE_ID` remains a temporary compatibility alias for
the test table during migration; do not use it as the production value.

## Submission Tracker Setup

The existing Applications data source remains compatible with importing,
scoring, Telegram review, and CV drafting. Before enabling an actual delivery
route, add these three properties to the Applications data source:

| Property | Type | Suggested options / purpose |
| --- | --- | --- |
| Application URL | URL | Verified direct company portal URL; separate from the discovery source |
| Submission Channel | Select | `email`, `portal`, `whatsapp` |
| Contact Source | Text | `job_post` or `official_site` plus the reviewed source context |
| Submission Record | Text | Message ID or confirmed portal/WhatsApp audit detail and CV draft ID |

The agent checks for these properties immediately before sending Gmail. It will
not send if the audit fields are absent.

To create the missing fields on the configured Notion data source, run:

```bash
uv run --no-editable backend-scout notion add-submission-fields --tracker production
```

The command is additive: it creates only missing fields and refuses to replace
an existing field with the wrong type.

To turn a reviewed test job into a real application, use promotion rather than
copying a Notion row by hand. Promotion re-scores the job, creates a fresh
production `found` row, and sends a new production Telegram digest:

```bash
uv run --no-editable backend-scout jobs promote TEST_PAGE_ID \
  --application-url https://company.example/careers/job-id \
  --chat-id TELEGRAM_CHAT_ID
```

The original discovery URL remains in `Source URL`; browser submission uses
`Application URL`.

## Gmail Setup

1. In Google Cloud Console, create or select a project and enable the Gmail API.
2. Configure the OAuth consent screen for your own Google account.
3. Create an OAuth **Desktop app** client and download its JSON file to a private
   local directory outside this repository.
4. Put its absolute path in `GMAIL_OAUTH_CLIENT_SECRET_PATH` in `.env`.
5. Run `uv run --no-editable backend-scout gmail connect` and complete the local
   Google consent screen.
6. Confirm with `uv run --no-editable backend-scout gmail check`.

The integration requests only these Gmail scopes:

```text
https://www.googleapis.com/auth/gmail.send
https://www.googleapis.com/auth/gmail.readonly
```

The OAuth refresh token is stored in macOS Keychain under BackendScout and is
not written to `.env`, this repository, or Notion.

After adding the readonly scope, rerun `gmail connect` once so Google issues a
token that can both send approved messages and classify incoming application
updates.

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

For portal delivery, run `apply request-submit` after browser preparation. The
Telegram `Submit now` action is valid for 15 minutes, tied to the exact CV PDF
and portal URL, and consumed only when BackendScout is about to click a visible
submit control.

WhatsApp is deliberately a handoff only. The system may show a verified public
number and prepare a message, but it never operates a personal WhatsApp account
or records a WhatsApp submission until the user confirms it.

## Continuous Telegram Listener

The production listener uses Telegram long polling and macOS `launchd`. It
processes authorized buttons plus `/status`, `/scout`, `/tailor_...`, and
`/revise_...` commands as they arrive, so routine workflow transitions do not
require a terminal command. It does not interpret ordinary text as
authorization and cannot submit an application by itself.

1. Edit `launchd/com.backendscout.telegram.plist.template` and replace every
   `TODO_ABSOLUTE_PROJECT_PATH` with `/Users/sagi/Documents/CV/BackendScout`.
2. Copy the edited file to
   `~/Library/LaunchAgents/com.backendscout.telegram.plist`.
3. Make the runner executable and load it:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.backendscout.telegram.plist
```

The listener runs the following same command that can be tested manually:

```bash
uv --cache-dir .uv-cache run --no-editable backend-scout telegram listen \
  --tracker production --timeout-seconds 30
```

It writes ignored logs under `data/logs/`. To stop it later:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.backendscout.telegram.plist
```

## Daily Local Scheduler

BackendScout uses macOS `launchd` for a local morning job collection. This is
more reliable than `cron` on a laptop because it is managed by macOS and writes
separate standard-output/error logs.

1. Edit `launchd/com.backendscout.daily.plist.template` and replace every
   `TODO_ABSOLUTE_PROJECT_PATH` with `/Users/sagi/Documents/CV/BackendScout`.
2. Create the local LaunchAgents directory if needed:

```bash
mkdir -p ~/Library/LaunchAgents
```

3. Copy the edited file to `~/Library/LaunchAgents/com.backendscout.daily.plist`.
4. Make the runner executable:

```bash
chmod +x scripts/run_daily_collection.sh
```

5. Load the agent:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.backendscout.daily.plist
```

It runs daily at 08:00 local time. The exact manual and scheduled command is:

```bash
uv --cache-dir .uv-cache run --no-editable backend-scout collect run \
  --tracker production --write-notion --send-digest
```

Logs are stored in ignored `data/logs/`. To test the job immediately, run the
command above in the project terminal; to unload the schedule later, run:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.backendscout.daily.plist
```

## Mailbox Watcher Scheduler

The mailbox watcher runs every 15 minutes and uses Gmail readonly to detect
status changes. It writes to the production tracker only when the signal is
clear and the message can be matched to an existing application.

1. Edit `launchd/com.backendscout.mailbox.plist.template` and replace
   `TODO_ABSOLUTE_PROJECT_PATH` with `/Users/sagi/Documents/CV/BackendScout`.
2. Copy it to `~/Library/LaunchAgents/com.backendscout.mailbox.plist`.
3. Make the runner executable:

```bash
chmod +x scripts/run_mailbox_watcher.sh
```

4. Load the watcher:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.backendscout.mailbox.plist
```

The manual command for one preview run is:

```bash
uv --cache-dir .uv-cache run --no-editable backend-scout gmail watch-once
```

The production write command used by the watcher is:

```bash
uv --cache-dir .uv-cache run --no-editable backend-scout gmail watch-once \
  --tracker production --write-notion --send-digest
```

## Repository Evidence Scanner

Start from the example and keep the real file private:

```bash
cp config/repo_sources.example.yaml config/repo_sources.yaml
```

Fill only the local repo roots and public GitHub owners you want scanned. The
scanner is read-only and creates proposals, not automatic CV claims:

```bash
uv --cache-dir .uv-cache run --no-editable backend-scout repos validate
uv --cache-dir .uv-cache run --no-editable backend-scout repos scan --send-telegram --tracker production
```

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
```

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
