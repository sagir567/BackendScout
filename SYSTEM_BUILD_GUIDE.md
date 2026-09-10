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
| Application URL | URL, required before portal preparation |
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
| Submission Channel | Select, required only before real delivery |
| Contact Source | Text, required only before real delivery |
| Submission Record | Text, required only before real delivery |

Use `notion add-submission-fields --tracker production` to add the application
URL and delivery-only columns to the production data source. It is idempotent
and refuses type conflicts.

Validation command:

```bash
uv run --no-editable backend-scout notion check --tracker test
```

## Phase 4: Job Collection

Start simple. Do not scrape everything at once.

Collector order:

1. Manual import from pasted URL/text.
2. Public company career pages.
3. Greenhouse/Lever/Ashby-style public job pages and reviewed Israeli aggregators.
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
- [x] Add first public collector.
```

Manual import commands:

```bash
uv run --no-editable backend-scout jobs validate examples/manual_job.example.yaml
uv run --no-editable backend-scout jobs import examples/manual_job.example.yaml
uv run --no-editable backend-scout jobs import data/raw/company-role.yaml --write-notion
```

The import command is preview-only unless `--write-notion` is passed.

Public ATS collector progress:

- [x] Add Greenhouse, Lever, and Ashby collectors that use published job-board
  JSON only; none calls an ATS application endpoint.
- [x] Add Lin-Srael as a low-volume, keyword-configured Israeli discovery
  source. Preserve its listing URL and use only a published external employer
  URL as the application target.
- [x] Add a private `target_companies.yaml` worksheet with enabled/disabled
  boards and Israel-relevant starter companies.
- [x] Filter collection results to Israel locations or remote roles before
  transparent scoring and Notion sync.
- [x] Extract deterministic skill and years-of-experience signals from public
  posting descriptions so ATS feeds without structured requirements still score
  usefully.
- [x] Keep the automatic shortlist high-precision by requiring meaningful
  target-role relevance before a collected job enters the digest.
- [x] Add optional private `scouting_preferences.yaml` controls for preferred,
  maybe, and excluded title keywords plus minimum score/relevance thresholds.
- [x] Add a `launchd` template that invokes the same tested CLI command used
  for a manual daily collection.
- [x] Add `system launchd install/status/uninstall` commands so the local
  Telegram listener, worker, mailbox watcher, and daily scout can be installed
  from the same verified CLI.
- [x] Add a reviewed Israeli aggregator source with a public feed, source limits,
  and cross-query deduplication.

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
- `telegram listen --tracker production` continuously long-polls the production
  workflow. `scripts/run_telegram_listener.sh` and its `launchd` template keep
  it alive across terminal sessions and write only local ignored logs.
- The listener processes only authorized callback buttons and the explicit
  `/tailor_<page-id>`, `/revise_<page-id>`, `/draft_<page-id>`,
  `/prepare_<page-id>`, `/status`, `/jobs`, `/today`, `/submit_status`, and
  `/scout` commands. Free-form messages do not authorize delivery or external
  submission.
- `/jobs` lists actionable open applications, `/today` lists recent tracked
  jobs, and `/submit_status` lists jobs ready for portal preparation or final
  submit progress.
- Telegram callback queries are acknowledged immediately before slower Notion,
  Gmail, or browser work starts, so buttons should stop blinking quickly.
- `Approve tailoring` can create a local queued CV draft task, and `/scout`,
  `/draft_<page-id>`, and `/prepare_<page-id>` enqueue the corresponding long
  work. `tasks worker-once` processes one task at a time; the launchd worker
  runs the same command every minute with a local lock.
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

Current implementation as of 2026-09-01:

- A private `config/career_evidence.yaml` ledger is the only permitted source for
  CV claims. The committed example contains placeholders only.
- The OpenAI Responses API returns a structured draft with evidence IDs attached
  to every summary and bullet; unsupported skills or citations are rejected.
- `cv draft <notion-page-id>` only works from `approved_to_tailor` and produces
  local DOCX and PDF files under the configured private archive root.
- The artifact manifest stores a SHA-256 digest. Telegram's `Approve this CV`
  action verifies that exact digest before transitioning to `approved_to_submit`.
- The model response uses strict structured output. Every generated statement is
  tied to private evidence IDs before document rendering.
- A private `config/cv_style.yaml` presentation contract is included with the
  model input and enforced again by the DOCX renderer. It currently hides the
  headline under the candidate name and prohibits visible raw URLs.
- DOCX contact and project links render as labeled external hyperlinks, such as
  `GitHub` and `LinkedIn`, rather than printing full URLs.
- The final PDF must be exactly one page and use at least 90% of the usable
  page height. BackendScout measures the rendered PDF locally with pdfplumber,
  selects the fullest valid layout candidate, and fails before delivery if it
  cannot satisfy both conditions.
- Before drafting, `cv coverage <notion-page-id>` measures the factual evidence
  ledger against the job's explicit requirements. Its 90/100 target is separate
  from the match score: tailoring can improve presentation, but it cannot make a
  job more suitable or introduce unsupported qualifications. Below target, CV
  creation pauses and produces targeted factual questions. Confirmed answers are
  added to the private evidence ledger; a skill gap remains open until backed by
  real work or a completed project.
- The first version presents coverage questions in the active conversation. Its
  result is transport-neutral so Telegram can send the same questions and store
  the answers in a later approval-loop slice.
- The Telegram document sender uses multipart uploads and redacts request URLs
  from failures so a bot token is never printed in an error message.
- A live test created and delivered a one-page DOCX/PDF pair to the configured
  Telegram chat, then verified the exact artifact checksum and `cv_drafted`
  Notion status. Final approval remains human-triggered.
- A live black-box acceptance test on 2026-09-02 exercised a fictional backend
  job through scoring, Notion import, Telegram digest approval, OpenAI drafting,
  DOCX/PDF rendering, Telegram delivery, checksum verification, and a Notion
  audit that confirmed no submission occurred.
- Telegram may reject acknowledgement of an old button click. The callback UI
  acknowledgement is therefore best-effort after the durable Notion transition;
  a stale acknowledgement cannot block the workflow or leave its offset stuck.
- `Request changes` transitions `cv_drafted -> revision_requested`; feedback is
  saved privately, and `cv revise` creates a new `vN` archive directory. The
  former draft ID cannot approve the newer CV.
- Link labels are set per URL in the private `cv_style.yaml`, which allows
  personal GitHub, organization GitHub, LinkedIn, and project links to remain
  distinct without exposing raw URLs.
- The private `tailoring_guidance` list in `cv_style.yaml` holds reusable
  precision rules. It can describe how verified technologies should be named,
  but cannot authorize unsupported claims.
- Before a draft exists, an authorized user can send
  `/tailor_<page-id> <instruction>` in Telegram. The private note is scoped to
  the selected tracker and incorporated into the next draft as emphasis-only
  guidance. Its SHA-256 digest is retained in the immutable draft manifest.

## Phase 8: Submission And Tracking

The submitter should be conservative.

Allowed:

- Prepare application material.
- Open browser-assisted flows.
- Fill simple forms after approval where allowed.
- Update Notion after approval.
- Send a reviewed email after a final Telegram email-review approval.

Not allowed:

- Submit without approval.
- Bypass anti-bot systems.
- Misrepresent user data.
- Guess employer contact addresses or numbers.
- Automate a personal WhatsApp account.

Current implementation:

- Two Notion data sources separate testing from real applications. Tracker-aware
  commands default to `test`; production commands must pass
  `--tracker production`. Telegram callback payloads include a compact tracker
  code and reject cross-tracker actions.
- `jobs promote` copies one test row to a fresh production workflow only after a
  verified direct company `Application URL` is supplied. It preserves the
  discovery `Source URL`, recalculates the score, and requires new approvals.
- `notion add-submission-fields` provisions the delivery properties and every
  workflow value used by the status machine. It preserves existing Notion
  status options while adding only missing values.

- Contact discovery accepts only details visibly present in the job description
  and an explicit official company careers/contact URL. It saves a private local
  record before an email review may name a recipient.
- Gmail uses a local OAuth desktop flow with `gmail.send` and `gmail.readonly`;
  its refresh token is held in macOS Keychain. Send is used only for approved
  outreach, and readonly is used for inbox status tracking. Telegram shows the
  recipient, source, exact body, and approved attachment before the delivery
  button becomes available.
- A Gmail success writes `submitted`, `email`, contact source, Gmail message ID,
  timestamp context, and exact CV draft ID to Notion.
- Portal preparation uses a dedicated visible Playwright profile. It fills only
  clear name/email/phone/location fields and a file upload, then stops for all
  ambiguous questions. CAPTCHA detection transitions to
  `awaiting_human_verification`; Chrome Remote Desktop is human-operated.
- The portal submit path verifies that the exact approved PDF is attached to
  the Resume/CV input in the active browser session. Missing required fields or
  an unverified attachment prevent the submit click.
- When an official careers page uses a public `Apply Now` link to hand off to
  an ATS, portal preparation follows that navigation before field detection.
- Ambiguous form answers are saved only after candidate confirmation, in a
  private per-application record that is bound to its Notion page and tracker.
- A candidate may explicitly save a response as private reusable guidance. It
  remains editable per role and never overrides job-specific candidate input.
- Portal adapters prefer accessible field labels and native file inputs over
  generated DOM IDs, including Wix-hosted forms.
- `apply request-submit` sends a final Telegram review that names the company,
  role, portal host, and exact CV draft. Its private authorization is bound to
  the page, tracker, portal URL, DOCX checksum, and PDF checksum; it expires
  after 15 minutes and can be consumed once.
- `apply resume` may click an unambiguous submit control only after that final
  Telegram approval. If a CAPTCHA appears before or after that click, it keeps
  the exact visible browser session open for the configured remote-verification
  wait window (600 seconds by default), without solving or inspecting the
  challenge. Resuming from `awaiting_human_verification` passes the same wait
  window instead of closing the browser immediately. It records a portal
  submission only when the page visibly confirms it; a click without
  confirmation stays `submission_prepared` and consumes the approval
  conservatively.
- CAPTCHA-solving services, challenge-token replay, browser-fingerprint
  evasion, and undocumented ATS submission endpoints are outside the system's
  design. Official ATS write APIs are used only when the employer or an
  approved integration supplies credentials for that exact purpose. Public job
  board APIs remain suitable for discovery and form-schema inspection.
- `scripts/blackbox_verification_handoff.py` exercises the real headed browser
  against a local fixture whose simulated verification clears after 1.5
  seconds. It proves that the same browser context remains alive and continues
  filling the form without contacting an employer portal.
- When Tailscale is active, the verification wait starts a temporary HTTP
  controller bound only to the Mac's `100.64.0.0/10` Tailscale address. A
  256-bit random path token is sent through Telegram. The controller serves a
  cached screenshot of the active Playwright viewport and queues human touch,
  scroll, key, and typing actions; only the Playwright-owning thread applies
  those actions. It stops when verification clears or the wait expires.
- The handoff refuses wildcard, public, and LAN bindings. The phone must be
  signed into the same tailnet. `system tailscale check` validates the Mac side
  before a live application.
- `scripts/blackbox_tailscale_handoff.py` performs an isolated HTTP black-box
  test over loopback, including the secret URL, cached frame, rejected token,
  and queued touch action. Loopback is available only through the test-only
  constructor switch; production integration still requires a Tailscale IP.
- On confirmed portal success, BackendScout captures a full-page screenshot,
  stores it under the private proof root, hashes it, sends it to Telegram, and
  appends the proof path/checksum to Notion's submission record.
- WhatsApp remains prepared-user-send only. It is never auto-messaged.

## Phase 9: Interview Prep

After approval or submission, generate a prep packet:

- Role-specific backend questions.
- System design prompts.
- Missing skills checklist.
- Mini-project ideas if a gap is meaningful.

## Phase 10: Repository Evidence And Inbox Intelligence

The agent should become better at learning from existing proof without making
unsupported claims.

Repository scanner rules:

- Scan only configured local Git roots and public GitHub owners.
- Detect languages, frameworks, dependency files, Docker, CI/CD, test tools, and
  high-level project metadata.
- Produce proposals only. A detected technology is not added to the profile or
  evidence ledger until approved in Telegram.
- Use repository evidence to ask better factual questions, not to inflate CV
  claims.

Inbox watcher rules:

- Use Gmail readonly metadata/snippets to classify obvious application updates.
- Mark confirmations, rejections, assessments, interviews, and offers only when
  the signal is clear and the email matches an existing application.
- Send morning digest updates by default; notify immediately only for messages
  that need action, such as an interview or assessment.
- Keep ambiguous recruiting messages as review items instead of changing Notion.

## Current Next Checkpoint

TODO:

```text
- [x] Add a CV revision path so a candidate can request a corrected draft before final approval.
- [x] Support custom labels for multiple links from the same service, for example
  personal GitHub versus an organization or project GitHub.
- [x] Add reviewed Gmail delivery with a Keychain-held OAuth token.
- [x] Add contact discovery limited to the job post and explicit official company pages.
- [x] Add conservative portal preparation, a human-verification workflow state,
  and a one-time final Telegram submit authorization tied to the exact CV files.
- [x] Add public Greenhouse, Lever, and Ashby collectors for Israel-relevant roles.
- [x] Add Lin-Srael keyword scouting with employer-link preservation and deduplication.
- [x] Add a digest command that chooses the target Telegram chat automatically from config.
- [x] Add test/production Notion tracker separation and explicit promotion.
- [x] Add private global and Telegram per-job CV tailoring guidance.
- [x] Add Linux/HPC and multithreading evidence coverage for the Infinidat role.
- [x] Add read-only repository scanning with proposal reports and Telegram review.
- [x] Add fast Telegram callback acknowledgements, `/status`, `/scout`, and a local task queue.
- [x] Add portal proof screenshot capture and Notion audit fields.
- [x] Add Gmail readonly mailbox classification and a 15-minute launchd watcher template.
- [x] Append mailbox status-change audit records to Notion so each update keeps
  the Gmail message ID, subject, sender, reason, and timestamp.
- [ ] Add job-specific interview preparation packets after tailoring approval.
- [x] Keep submission disabled until `approved_to_submit`.
```
