# BackendScout Agent Rules

These rules apply to all work in this repository.

## Architecture and Autonomous Execution

- Start with `ARCHITECTURE.md` baseline 1.1 and `EXECUTION_PLAN.yaml`. They are the
  current implementation contract; older research documents are background.
- Run `python scripts/project_status.py --check-evidence` to identify ready work.
  Select a dependency-ready autonomous task and continue through implementation,
  verification, review and evidence recording. Do not stop after proposing work.
- Preserve the frozen stack, product scope and 100 NIS monthly ceiling. Routine
  fixes are permitted; a changed baseline decision needs an owner-directed
  amendment and a new version/checksum, not a silent redesign.
- User-authorized subagents may implement independent tasks concurrently. Give
  each a disjoint write scope and acceptance criteria. Integration owns shared
  interfaces and the plan file. Review returned changes before recording done.
- Update task status incrementally and attach observed acceptance evidence.
  A blocked external task does not prevent progress on independent ready work.
- Seller onboarding, provider purchases, actual application delivery and public
  publication follow existing owner authorization. A task ledger is not an
  approval for an external action. No background development is implied merely
  by leaving a task pending in the file.

## User Approval

- Ask the user before choosing or changing meaningful technologies, services, frameworks, databases, APIs, providers, hosting, or automation tools.
- Explain the tradeoffs briefly before asking for a decision.
- The owner granted standing approval on 2026-11-05 to commit and push coherent,
  tested milestones to the existing BackendScout remote. Use scoped branches
  when useful and scan staged changes for secrets. Never force-push or include
  private profiles, credentials, CVs or docs. Employer delivery still needs its
  own exact-package approval; provider changes still require owner agreement.
- Do not submit job applications, send recruiter messages, upload CVs, or mark an application as submitted without explicit user approval.

## Storage Preference

- Use SQLite/DBOS as the approved operational workflow store. Keep Notion as the
  human-facing dashboard and synchronized projection.
- Do not create a second authoritative workflow store.

## Existing CV Archive

- Do not reorganize or overwrite the parent CV folder.
- Approved submitted CV versions should be stored in a company-named folder under the existing CV archive when that workflow is implemented.

## Authenticity

- Before asking repeated application questions, consult the ignored
  `config/application_facts.private.yaml` when present. Respect each fact's date,
  scope and limits. Facts never replace exact-package approval or new legal consent.
- Keep all CV and application content truthful and explainable by the user in an interview.
- Do not invent experience or add deliberate mistakes to disguise AI usage.

## Teaching Style

- When explaining something the user is learning, give direct, step-by-step guidance.
- When useful, create partial files with `TODO` placeholders for the user to fill in.
- Prefer small learning checkpoints over large abstract explanations.

## Documentation Policy

- Do not commit the `docs/` folder to git. Treat it as local scratch notes and worksheets.
- Continuously update `README.md` with the current project state and setup commands.
- Continuously update `SYSTEM_BUILD_GUIDE.md` with the learning-oriented instructions for building this complex agent system.
- Continuously update `REQUIREMENTS.md` and `requirements.txt` when install requirements or dependencies change.
