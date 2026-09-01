# Building BackendScout

This guide explains how to build BackendScout as a complex local-first agent
system. Keep it updated as the project evolves.

## What We Are Building

BackendScout is a job-search agent for backend engineering roles. It should:

1. Scan relevant job sources.
2. Parse and normalize job descriptions.
3. Score each role against a truthful candidate profile.
4. Send a daily digest for human review.
5. Tailor CV drafts only after approval.
6. Submit applications only after final approval.
7. Track every job and application in Notion.
8. Generate interview preparation tasks for approved applications.

## Core Design Principles

- Human approval is part of the architecture, not an afterthought.
- The tracker source of truth is Notion.
- The CV archive stays outside this repo and is configured locally through
  `CV_ARCHIVE_ROOT`.
- The system must be truthful and interview-explainable.
- Each agent should have a narrow job and structured inputs/outputs.
- External actions should be wrapped in explicit tools.
- Every important state transition should be observable in Notion.

## Current Architecture

```text
Daily Scheduler
  -> Job Collectors
  -> Job Parser
  -> Matcher
  -> Telegram Digest
  -> Human Approval
  -> CV Tailor
  -> Final Human Approval
  -> Submitter
  -> Notion Tracker
  -> Prep Coach
```

## Repo Files To Keep Updated

- `README.md`: current setup, commands, and project state.
- `SYSTEM_BUILD_GUIDE.md`: learning guide and build instructions.
- `REQUIREMENTS.md`: system requirements, install commands, and external services.
- `requirements.txt`: pip-compatible entry point for installing from `pyproject.toml`.
- `AGENTS.md`: rules for Codex and future agents working in this repo.

Local-only notes belong in `docs/` and are not committed.

## Phase 1: Foundation

Goal: make the repo runnable and connect the first external system.

Completed:

- Created `BackendScout` as a separate git repo.
- Added approval and documentation rules in `AGENTS.md`.
- Chose a Notion-first tracker after comparing storage approaches.
- Chose `uv` as the official Python/project environment manager.
- Connected a Notion `Applications` data source.
- Added Notion schema validation.
- Added root-level runtime requirements documentation.
- Pushed the foundation to GitHub after explicit approval.

TODO:

```text
- [ ] Install uv.
- [ ] Run uv sync --extra dev --no-editable --link-mode copy.
- [ ] Run uv run --no-editable backend-scout notion check.
- [ ] Create the real candidate profile file from config/candidate_profile.example.yaml.
```

macOS note for this machine:

If the repo stays under `Documents` and `.venv` inherits File Provider metadata,
keep the real virtualenv outside `Documents` and leave `.venv` as a symlink:

```bash
mv .venv .venv.documents-backup-2026-08-31
uv venv /private/tmp/backendscout-venv
ln -s /private/tmp/backendscout-venv .venv
uv sync --extra dev --no-editable --link-mode copy
```

## Phase 2: Candidate Profile

The candidate profile should be structured, truthful, and easy to inspect.

Inputs:

- Target roles.
- Locations and remote/hybrid preferences.
- Salary floor or compensation preferences.
- Real skills.
- Real projects and experience.
- Constraints, such as roles to avoid.

Output file:

```text
config/candidate_profile.yaml
```

This file is ignored by git because it contains real personal data.

TODO template:

```yaml
name: TODO
target_roles:
  - Backend Engineer
target_locations:
  - TODO
salary_floor_nis: TODO
work_preferences:
  remote: true
  hybrid: true
  onsite: false
core_skills:
  - TODO
proof_points:
  - TODO real project or work example
constraints:
  require_truthful_cv_only: true
  require_approval_before_submit: true
```

Validation command:

```bash
uv run --no-editable backend-scout profile check
```

## Phase 3: Notion Tracker

Notion is the human-facing source of truth. The agent can read and write the
`Applications` data source, but critical state changes still need approval.

Required properties:

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

Validation command:

```bash
uv run --no-editable backend-scout notion check
```

## Phase 4: Job Collection

Start simple. Do not scrape everything at once.

Collector order:

1. Manual import from pasted URL/text.
2. Public company career pages.
3. Greenhouse/Lever/Ashby-style public job pages.
4. Browser-assisted sources only when needed.

Rules:

- Do not bypass CAPTCHA.
- Do not bypass login restrictions.
- Respect job-site terms and rate limits.
- Store only the data needed for matching and tracking.

TODO:

```text
- [x] Add manual import command.
- [x] Add normalized Job model fields for parser output.
- [x] Add dedupe key strategy.
- [ ] Add first public collector.
```

Manual import commands:

```bash
uv run --no-editable backend-scout jobs validate examples/manual_job.example.yaml
uv run --no-editable backend-scout jobs import examples/manual_job.example.yaml
uv run --no-editable backend-scout jobs import data/raw/company-role.yaml --write-notion
```

The import command is preview-only unless `--write-notion` is passed.

Current import behavior:

- `jobs score` gives a deterministic shortlist view without external writes.
- `jobs import --write-notion` rescoring is always based on the latest local
  candidate profile.
- Duplicate copies of the same job in one YAML file are skipped during sync.
- Existing Notion rows are updated in place when the same job is imported again.
- Existing workflow status is preserved during updates so approval gates stay intact.

## Phase 5: Matching Agent

The matcher should return structured reasoning, not just a score.

Expected output:

```json
{
  "match_score": 0,
  "fit_summary": "TODO",
  "matched_skills": [],
  "missing_skills": [],
  "concerns": [],
  "recommended_action": "skip|maybe|apply"
}
```

Scoring should consider:

- Backend relevance.
- Required experience.
- Skills overlap.
- Location and remote policy.
- Salary floor versus stated compensation.
- Company/domain interest.
- Salary if provided.
- Risk of mismatch or overreach.

Current progress as of 2026-08-31:

- [x] Deterministic scoring engine with transparent weighted breakdown.
- [x] Approval-gated status flow modeled before any future submission work.
- [x] `jobs score` CLI for shortlist-style review.
- [x] `jobs import --write-notion` now recomputes `Match Score` and `Match Reason`
  from the local candidate profile before syncing Notion rows.
- [x] Repeated imports dedupe identical jobs and update existing Notion rows.
- [x] Telegram digest send and one-shot approval polling over the raw Bot API.

## Phase 6: Human Approval Loop

Telegram will be the first approval channel.

Digest format:

```text
Company - Role
Score: 87%
Location: TODO
Remote: TODO
Key requirements: TODO
Why it fits: TODO
Action: approve / skip / maybe / details
```

Approval gates:

- Before tailoring a CV.
- Before sending or uploading a CV.
- Before sending recruiter messages.
- Before marking an application as submitted.

Current implementation as of 2026-08-31:

- `telegram check` verifies the bot token and prints configured allowed user IDs.
- `telegram peek-updates` shows pending Telegram user IDs and messages without
  touching Notion state or the stored offset.
- `telegram send-digest --chat-id <id>` pulls jobs from Notion by status and
  sends one digest message per job.
- Digest messages include inline buttons for `Approve tailoring` and `Close`.
- Sending a digest moves `found` jobs to `digest_sent`.
- `telegram poll-once` consumes Telegram callback updates once and stores the
  last processed Telegram update ID in `data/telegram/last_update_id.txt`.
- Only configured Telegram user IDs may trigger approval actions.
- `Approve tailoring` transitions `digest_sent -> approved_to_tailor`.
- `Close` transitions the current job to `closed` when that transition is valid.
- Automated tests cover digest formatting, inline buttons, authorized and unauthorized
  callbacks, the `found -> digest_sent` writeback, and the CLI handoff.
- A live test on 2026-09-01 confirmed the configured Notion test table can create,
  score, and send a test digest to the configured Telegram chat. The final callback
  transition remains intentionally human-triggered by pressing a Telegram button.

## Phase 7: CV Tailoring

The CV tailor can reorder, clarify, and emphasize real experience. It cannot
invent facts.

Inputs:

- Candidate profile.
- Base CV source.
- Job description.
- Match analysis.

Outputs:

- Tailored CV draft.
- Change summary.
- Risk report: anything that might sound unsupported.

Approval:

```text
No CV artifact is submitted or archived as submitted until the candidate approves it.
```

## Phase 8: Submission And Tracking

The submitter should be conservative.

Allowed:

- Prepare application material.
- Open browser-assisted flows.
- Fill simple forms after approval where allowed.
- Update Notion after approval.

Not allowed:

- Submit without approval.
- Bypass anti-bot systems.
- Misrepresent user data.

## Phase 9: Interview Prep

After approval or submission, generate a prep packet:

- Role-specific backend questions.
- System design prompts.
- Missing skills checklist.
- Mini-project ideas if a gap is meaningful.

## Current Next Checkpoint

TODO:

```text
- [ ] Add a first collector for one public job source.
- [ ] Add CV draft file generation for `approved_to_tailor`.
- [ ] Add a digest command that chooses the target Telegram chat automatically from config.
- [ ] Add richer approval actions after CV generation, including `approved_to_submit`.
- [ ] Keep submission disabled until `approved_to_submit`.
```
