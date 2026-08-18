# Implementation Plan

## Phase 1: Foundation

- Create project repo.
- Define candidate profile schema.
- Define job and application tracker schema.
- Add CLI commands for profile, scan, digest, and status.
- Add configuration and secrets templates.

## Phase 2: Job Discovery MVP

- Add manual job import from URL/text.
- Add one or two public-source collectors.
- Add deduplication by canonical URL, company, title, and content hash.
- Add structured parsing and match scoring.

## Phase 3: Telegram Approval Loop

- Send daily digest to Telegram.
- Support commands: `approve`, `skip`, `maybe`, `details`, `status`.
- Persist every approval decision.

## Phase 4: CV Tailoring

- Convert the base CV into a structured source format.
- Generate role-specific CV drafts.
- Produce PDF artifacts.
- Save approved versions in company-named archive folders.

## Phase 5: Application Tracker

- Track every job from discovery to final outcome.
- Produce weekly metrics.
- Generate interview preparation tasks after each approved application.

