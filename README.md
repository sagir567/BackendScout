# BackendScout

BackendScout is a local-first job-search agent for backend engineering roles.

The project has two goals:

1. Practice building useful AI agents with real tools, state, approvals, and scheduled workflows.
2. Make the job-search process calmer and more systematic: find relevant roles, score fit, request approval, tailor truthful CV versions, track submissions, and prepare for interviews.

## Current Approach

- Core orchestration: Codex + OpenAI Agents SDK.
- Runtime: Python.
- State: SQLite at first, with optional Notion or Google Sheets sync later.
- Human approval: Telegram first.
- CV archive: existing CV folders stay outside this repo. Submitted CV versions can continue to be stored in company-named folders in `/Users/sagi/Documents/CV`.

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

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
backend-scout --help
```

