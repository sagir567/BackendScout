# BackendScout Requirements

This file is the install/run checklist for BackendScout. Keep it updated when
we add tools, services, APIs, packages, or system dependencies.

## Current Runtime Requirements

| Requirement | Status | Notes |
| --- | --- | --- |
| macOS | Required | Current development machine is Sagi's Mac. |
| Python | Required | Python 3.12, pinned by `.python-version`. |
| uv | Required | Official Python/project environment manager. |
| Notion account | Required | Used as the human-facing application tracker. |
| Notion internal integration | Required | Must have access to the `Applications` data source. |
| OpenAI API key | Planned | Needed once we add agentic parsing, matching, and CV tailoring. |
| Telegram bot token | Planned | Needed once we add human approval via Telegram. |

## Python Package Requirements

The canonical Python dependency list lives in `pyproject.toml`.

The official install command is:

```bash
uv sync --extra dev
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
CV_ARCHIVE_ROOT=/Users/sagi/Documents/CV
```

Planned values:

```bash
OPENAI_API_KEY=TODO
TELEGRAM_BOT_TOKEN=TODO
TELEGRAM_ALLOWED_USER_IDS=TODO
```

Do not commit `.env`.

## Official Setup: uv

```bash
brew install uv
uv sync --extra dev
uv run backend-scout --help
uv run backend-scout notion check
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

If that happens, run:

```bash
chflags -R nohidden .venv
uv sync --extra dev --reinstall-package backend-scout
```

After that, these commands should work:

```bash
uv run backend-scout --help
uv run pytest
uv run backend-scout notion check
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
```

Pros:

- Familiar Python workflow.
- Uses system-visible Python installed by Homebrew.

Tradeoffs:

- You need to manage `.venv` activation.
- Reproducibility is weaker than the `uv.lock` workflow.

## Current Verification Commands

These work before the full environment is installed when using Codex's bundled
Python runtime:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/backendscout_pycache \
  /Users/sagi/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m compileall src tests

PYTHONPATH=src \
  /Users/sagi/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -c "from tests.test_models import test_application_statuses_include_approval_gates, test_job_match_score_is_bounded; from tests.test_notion import test_build_job_page_properties_uses_data_source_schema_names, test_validate_applications_data_source_accepts_expected_schema, test_validate_applications_data_source_reports_missing_or_wrong_properties; test_application_statuses_include_approval_gates(); test_job_match_score_is_bounded(); test_build_job_page_properties_uses_data_source_schema_names(); test_validate_applications_data_source_accepts_expected_schema(); test_validate_applications_data_source_reports_missing_or_wrong_properties(); print('lightweight tests passed')"
```

After dependencies are installed, prefer:

```bash
uv run ruff check
uv run pytest
uv run backend-scout notion check
```
