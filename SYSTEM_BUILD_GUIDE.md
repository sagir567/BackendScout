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
- The CV archive stays outside this repo at `/Users/sagi/Documents/CV`.
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

TODO:

```text
- [ ] Install uv.
- [ ] Run uv sync --extra dev.
- [ ] Run uv run backend-scout notion check.
- [ ] Create the real candidate profile file from config/candidate_profile.example.yaml.
```

## Phase 2: Candidate Profile

The candidate profile should be structured, truthful, and easy to inspect.

Inputs:

- Target roles.
- Locations and remote/hybrid preferences.
- Real skills.
- Real projects and experience.
- Constraints, such as roles to avoid.

Output file:

```text
config/candidate_profile.yaml
```

TODO template:

```yaml
name: TODO
target_roles:
  - Backend Engineer
target_locations:
  - TODO
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
uv run backend-scout notion check
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
- [ ] Add manual import command.
- [ ] Add normalized Job model fields for parser output.
- [ ] Add dedupe key strategy.
- [ ] Add first public collector.
```

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
- Company/domain interest.
- Salary if provided.
- Risk of mismatch or overreach.

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
No CV artifact is submitted or archived as submitted until Sagi approves it.
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
- [ ] Install uv locally.
- [ ] Generate uv.lock.
- [ ] If macOS hidden flags break editable imports, run chflags -R nohidden .venv.
- [ ] Run uv run pytest.
- [ ] Run uv run backend-scout notion check.
```
