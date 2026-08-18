# Architecture

BackendScout is intentionally split into small modules instead of one all-powerful agent.

## Components

- `candidate_profile`: truthful source of candidate facts, preferences, constraints, and target roles.
- `collectors`: source-specific job discovery adapters.
- `parser`: turns raw job pages into structured jobs.
- `matcher`: scores job fit against the candidate profile.
- `approval`: human-in-the-loop gates for critical actions.
- `cv`: CV tailoring and export workflow.
- `tracker`: application state, history, and reports.
- `prep`: interview preparation recommendations.

## Daily Flow

1. Collect new backend roles.
2. Normalize and deduplicate jobs.
3. Score each role against the candidate profile.
4. Send a Telegram digest with the best matches.
5. Wait for explicit approval.
6. For approved jobs, draft a tailored CV and application summary.
7. Wait for final approval.
8. Submit only where allowed and technically safe.
9. Update tracker and generate preparation tasks.

## Approval Gates

BackendScout must request approval before:

- sending or submitting an application
- uploading a CV
- sending a recruiter message or email
- modifying a finalized CV artifact
- using browser automation on an authenticated site
- changing job status to `submitted`

