# BackendScout Architecture Research

## Executive Summary

BackendScout already has useful domain capabilities: deterministic matching,
Notion tracking, Telegram approvals, versioned CV artifacts, Gmail integration,
job collection, and browser-assisted submission. Its reliability problems come
from coordination rather than from those individual features.

Operational state is currently divided between Notion, local JSON files,
several launchd jobs, and browser sessions that are reopened for each command.
That makes it difficult to atomically answer which step ran, whether it may be
retried, and how to resume after a crash.

The replacement architecture uses:

- DBOS workflows backed by local SQLite for durable execution.
- Notion as the human-facing dashboard, synchronized from local state.
- Telegram as the primary command and approval interface.
- Deterministic Python services for permissions and state transitions.
- OpenAI Agents SDK and Codex for typed reasoning tasks, never as the source of
  submission authority.
- ATS-specific Playwright adapters with a guided fallback.

## Findings

### Queue and process model

The JSON task queue is rewritten as one document. It has no transactional task
claim, lease, recovery, retry schedule, or dead-letter state. A one-shot worker
processes one item per launchd interval, so unrelated work can delay urgent
portal actions.

The fitness agent's Telegram bridge offers a good conversational pattern, but
its queue exists only in process memory and every request launches an ephemeral
Codex run. That is suitable for a single-user spreadsheet assistant, not a
multi-day workflow with external side effects.

### Split authority

Notion currently carries business statuses while local files carry approvals,
answers, CV manifests, mailbox cursors, and queued work. A partial outage or
crash can update one store without updating another.

The deployed Application Support copy can also differ from the Git checkout.
Code is copied into the runtime while private state is preserved, making it
possible to inspect or test different code from the code launchd is executing.

### Browser lifecycle

Portal preparation launches a persistent Chromium profile but closes the
context after filling. Submission launches it again, navigates from the start,
and repeats form filling. The approved review therefore is not the exact page
instance later submitted.

Generic form and submit-control discovery is too broad for arbitrary career
sites. Redirects, overlays, iframe replacement, and company homepage CTAs can
look like application controls. A click without a detected confirmation also
creates an important unknown: retrying may submit twice.

### Observability and tests

Failures are usually reduced to a task ID and exception text. They are not
consistently connected to the company, application, browser screenshot, trace,
workflow step, and retry decision.

The unit suite is broad, but mocked browser and API tests cannot reproduce page
navigation, frame detachment, duplicate Telegram delivery, process death, or a
runtime upgrade during an approval wait.

## Architecture Decision

SQLite becomes the operational source of truth. Notion remains the preferred
dashboard and may accept reviewed user edits, but those edits enter the system
as validated commands before changing workflow state.

DBOS provides durable workflows, queues, retries, scheduling, and recovery on
top of SQLite. This is a better local-first fit than operating a Temporal server
and safer than implementing those guarantees again in custom queue code.

Codex remains valuable as a specialist for repository evidence discovery and
failure diagnosis. The OpenAI Agents SDK remains the application-level agent
runtime for job analysis, CV planning, and unfamiliar question classification.
Both produce typed proposals. Deterministic tools validate and execute them.

## Target Flow

```text
Telegram Gateway
  -> durable command/event
  -> application workflow
      -> collect and score
      -> wait for tailoring approval
      -> build and validate CV
      -> wait for exact-file approval
      -> prepare ATS-specific browser session
      -> wait for answers or verification
      -> wait for exact-submit approval
      -> submit once
      -> capture confirmation proof
  -> notification outbox
  -> Telegram and Notion projection
```

The browser queue has concurrency one. Scouting, mailbox, CV, and notification
queues are independent so portal work cannot delay the daily digest. Every
external update has an idempotency key. Every approval is one-use, revocable,
and bound to the tracker, application, URL, CV checksum, answers checksum, and
prepared-page fingerprint.

If a submit click may have reached the employer but no confirmation can be
proved, the application enters `submission_unknown`. It is never retried
automatically.

## Migration Strategy

1. Introduce SQLite and durable task compatibility while the existing launchd
   routines continue running.
2. Mirror/import current local task and application mappings into SQLite.
3. Move Telegram commands and callbacks to durable workflows with immediate
   acknowledgements.
4. Move daily scouting and mailbox scans to independent durable schedules.
5. Make Notion an outbox-driven projection after shadow-run parity succeeds.
6. Replace generic browser submission with Greenhouse, Comeet, and Lever
   adapters, then retain a non-submitting guided fallback.
7. Replace copied source deployments with versioned wheel releases and an
   atomic `current` pointer.

The current production morning scout stays enabled until the replacement has
passed a test run and a production shadow run. Migration failures must leave the
old routine available.

## Success Criteria

- Telegram callbacks are acknowledged within one second.
- A queued command is visible with company, role, state, and next action.
- Restarting the worker resumes from the last completed durable step.
- Duplicate Telegram, Gmail, and collector inputs do not duplicate actions.
- Portal work cannot delay scouting or mailbox processing.
- A submission is recorded only with a supported confirmation result and proof
  screenshot.
- Every failure has a correlation ID, application identity, step, retry status,
  and diagnostic artifact.
- The 08:00 Asia/Hebron digest runs once, or once after wake when configured for
  catch-up.

## Primary References

- [DBOS workflows](https://docs.dbos.dev/python/tutorials/workflow-tutorial)
- [DBOS queues](https://docs.dbos.dev/python/tutorials/queue-tutorial)
- [DBOS scheduling](https://docs.dbos.dev/python/tutorials/scheduled-workflows)
- [SQLite write-ahead logging](https://www.sqlite.org/wal.html)
- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
- [OpenAI Agents SDK human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)
- [Codex SDK](https://developers.openai.com/codex/sdk)
- [Playwright locators](https://playwright.dev/python/docs/locators)
- [Playwright Trace Viewer](https://playwright.dev/python/docs/trace-viewer)
- [Telegram Bot API](https://core.telegram.org/bots/api)
