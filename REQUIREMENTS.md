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
| OpenAI API key | Planned | Needed once we add agentic parsing, matching, and CV tailoring. |
| Telegram bot token | Required for approval loop | Used for the Telegram digest and approval commands. |
| Telegram allowed user IDs | Required for approval loop | Comma-separated numeric Telegram user IDs allowed to approve actions. |

## Python Package Requirements

The canonical Python dependency list lives in `pyproject.toml`.

Current runtime packages include Pydantic, Pydantic Settings, Typer, Rich,
HTTPX, Beautiful Soup, python-dotenv, and PyYAML.

The official install command is:

```bash
uv sync --extra dev --no-editable --link-mode copy
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
NOTION_API_KEY=TODO
NOTION_API_VERSION=2026-03-11
NOTION_APPLICATIONS_DATA_SOURCE_ID=TODO
TELEGRAM_BOT_TOKEN=TODO
TELEGRAM_ALLOWED_USER_IDS=TODO_COMMA_SEPARATED_NUMERIC_IDS
CV_ARCHIVE_ROOT=TODO_ABSOLUTE_PATH_TO_PRIVATE_CV_ARCHIVE
```

Planned values:

```bash
OPENAI_API_KEY=TODO
```

Do not commit `.env`.

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

## Local-Only Data Files

These files are intentionally ignored:

- `config/candidate_profile.yaml`: real candidate profile, salary floor, and preferences.
- `data/raw/`: real pasted job descriptions or manual job import files.
- `.env`: local API tokens and service IDs.
