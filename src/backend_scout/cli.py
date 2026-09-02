from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from backend_scout.candidate_profile import (
    DEFAULT_PROFILE_PATH,
    candidate_profile_warnings,
    load_candidate_profile,
)
from backend_scout.career_evidence import DEFAULT_CAREER_EVIDENCE_PATH, load_career_evidence
from backend_scout.config import Settings
from backend_scout.contact_records import require_verified_contact, save_contacts
from backend_scout.contacts import discover_job_post_contacts, discover_official_page_contacts
from backend_scout.cv_artifacts import (
    load_manifest,
    next_draft_directory,
    verify_manifest,
    write_manifest,
)
from backend_scout.cv_documents import convert_docx_to_pdf, create_cv_docx
from backend_scout.cv_style import DEFAULT_CV_STYLE_PATH, load_cv_style
from backend_scout.cv_tailoring import (
    build_evidence_only_draft,
    generate_tailored_cv,
    validate_tailored_cv_against_evidence,
)
from backend_scout.gmail import connect_gmail, gmail_connected, send_email
from backend_scout.job_imports import (
    job_with_match_result,
    load_manual_job_import,
    score_manual_job_import,
    unique_scored_jobs,
)
from backend_scout.matcher import ScoredJob, score_jobs
from backend_scout.models import ApplicationStatus, Job
from backend_scout.notion import (
    NotionClient,
    application_digest_item_from_page,
    job_from_application_page,
    list_jobs_by_status,
    missing_submission_property_definitions,
    upsert_job_page,
    validate_applications_data_source,
    validate_submission_data_source,
)
from backend_scout.outreach import build_email_review, load_email_review, save_email_review
from backend_scout.revisions import load_revision_feedback
from backend_scout.submission import prepare_visible_submission, submit_visible_submission
from backend_scout.telegram import (
    TelegramClient,
    build_cv_draft_reply_markup,
    build_email_review_reply_markup,
    build_whatsapp_handoff_reply_markup,
    process_telegram_update,
    send_digest_messages,
)
from backend_scout.whatsapp import (
    build_whatsapp_handoff,
    handoff_url,
    load_whatsapp_handoff,
    save_whatsapp_handoff,
)
from backend_scout.yaml_files import YamlFileError

app = typer.Typer(help="BackendScout job-search agent CLI.")
notion_app = typer.Typer(help="Notion integration commands.")
profile_app = typer.Typer(help="Candidate profile commands.")
jobs_app = typer.Typer(help="Manual job import commands.")
telegram_app = typer.Typer(help="Telegram approval commands.")
cv_app = typer.Typer(help="Truthful, approval-gated CV draft commands.")
gmail_app = typer.Typer(help="Gmail OAuth setup and reviewed email delivery.")
contacts_app = typer.Typer(help="Discover and review verified employer contacts.")
apply_app = typer.Typer(help="Prepare conservative browser-assisted submissions.")
console = Console()
TELEGRAM_OFFSET_PATH = Path("data/telegram/last_update_id.txt")

ProfilePathOption = Annotated[
    Path,
    typer.Option("--path", "-p", help="Candidate profile YAML path."),
]
ScoreProfilePathOption = Annotated[
    Path,
    typer.Option("--profile-path", "-p", help="Candidate profile YAML path for scoring."),
]
ManualJobPathArgument = Annotated[Path, typer.Argument(help="Manual job YAML path.")]
CareerEvidencePathOption = Annotated[
    Path,
    typer.Option("--evidence-path", "-e", help="Private career evidence YAML path."),
]
CvStylePathOption = Annotated[
    Path,
    typer.Option("--style-path", help="Private CV style YAML path."),
]


@app.command()
def status() -> None:
    """Show the current local configuration."""
    settings = Settings()

    table = Table(title="BackendScout")
    table.add_column("Setting")
    table.add_column("Value")
    table.add_row("Notion API version", settings.notion_api_version)
    table.add_row(
        "Notion applications data source",
        "configured" if settings.notion_applications_data_source_id else "missing",
    )
    table.add_row("Notion API key", "configured" if settings.notion_api_key else "missing")
    table.add_row("Telegram bot token", "configured" if settings.telegram_bot_token else "missing")
    table.add_row(
        "Telegram allowed user IDs",
        ",".join(str(user_id) for user_id in sorted(settings.telegram_allowed_user_id_set))
        or "missing",
    )
    table.add_row("CV archive root", str(settings.cv_archive_root))
    table.add_row("Fast model", settings.openai_model_fast)
    table.add_row("Balanced model", settings.openai_model_balanced)
    table.add_row("High quality model", settings.openai_model_high_quality)
    console.print(table)


@app.command()
def init_data() -> None:
    """Create local non-authoritative folders used by the project."""
    paths = [
        "data/raw",
        "data/exports",
        "data/telegram",
        "applications",
    ]
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)
        console.print(f"ready: {path}")


@app.command()
def statuses() -> None:
    """List supported application statuses."""
    for status_value in ApplicationStatus:
        console.print(status_value.value)


@profile_app.command("check")
def profile_check(path: ProfilePathOption = DEFAULT_PROFILE_PATH) -> None:
    """Validate the local candidate profile."""
    try:
        profile = load_candidate_profile(path)
    except (ValidationError, YamlFileError) as exc:
        _print_load_error("Candidate profile check failed", exc)
        raise typer.Exit(1) from exc

    console.print("[green]Candidate profile OK[/green]")
    console.print(f"Name: {profile.name}")
    console.print(f"Target roles: {', '.join(profile.target_roles)}")
    console.print(f"Core skills: {len(profile.core_skills)}")

    for warning in candidate_profile_warnings(profile):
        console.print(f"[yellow]Warning:[/yellow] {warning}")


@jobs_app.command("validate")
def jobs_validate(path: ManualJobPathArgument) -> None:
    """Validate a manual job import YAML file without writing to Notion."""
    try:
        manual_import = load_manual_job_import(path)
    except (ValidationError, YamlFileError) as exc:
        _print_load_error("Manual job file validation failed", exc)
        raise typer.Exit(1) from exc

    _print_jobs_table(manual_import.jobs, "Manual Job Import Preview")
    console.print(f"[green]Validated {len(manual_import.jobs)} job(s).[/green]")


@jobs_app.command("import")
def jobs_import(
    path: ManualJobPathArgument,
    profile_path: ScoreProfilePathOption = DEFAULT_PROFILE_PATH,
    write_notion: Annotated[
        bool,
        typer.Option(
            "--write-notion",
            help="Create Notion rows. Without this flag the command is preview-only.",
        ),
    ] = False,
) -> None:
    """Preview or write manually imported jobs to Notion."""
    try:
        manual_import = load_manual_job_import(path)
        profile = load_candidate_profile(profile_path)
    except (ValidationError, YamlFileError) as exc:
        _print_load_error("Manual job import failed", exc)
        raise typer.Exit(1) from exc

    scored_jobs = unique_scored_jobs(score_manual_job_import(profile, manual_import))
    _print_scored_jobs_table(scored_jobs, title="Manual Job Import Shortlist")

    if not write_notion:
        console.print(
            "[yellow]Preview only.[/yellow] Re-run with --write-notion to create Notion rows with fresh scoring."
        )
        return

    settings = Settings()
    if not settings.notion_api_key:
        console.print("[red]Missing NOTION_API_KEY in .env[/red]")
        raise typer.Exit(1)
    if not settings.notion_applications_data_source_id:
        console.print("[red]Missing NOTION_APPLICATIONS_DATA_SOURCE_ID in .env[/red]")
        raise typer.Exit(1)

    try:
        with NotionClient(
            api_key=settings.notion_api_key,
            api_version=settings.notion_api_version,
        ) as client:
            data_source = client.retrieve_data_source(settings.notion_applications_data_source_id)
            problems = validate_applications_data_source(data_source)
            if problems:
                console.print("[yellow]Connected to Notion, but the Applications schema needs fixes:[/yellow]")
                for problem in problems:
                    console.print(f"- {problem}")
                raise typer.Exit(1)

            actions = [
                upsert_job_page(
                    client,
                    settings.notion_applications_data_source_id,
                    job_with_match_result(scored_job.job, scored_job),
                )[0]
                for scored_job in scored_jobs
            ]
    except typer.Exit:
        raise
    except Exception as exc:
        console.print("[red]Notion import failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc

    created_count = actions.count("created")
    updated_count = actions.count("updated")
    console.print(
        f"[green]Synced {len(actions)} job(s) to Notion.[/green] "
        f"Created: {created_count}, Updated: {updated_count}."
    )


@jobs_app.command("score")
def jobs_score(
    path: ManualJobPathArgument,
    profile_path: ScoreProfilePathOption = DEFAULT_PROFILE_PATH,
) -> None:
    """Score manually imported jobs against the local candidate profile."""
    try:
        manual_import = load_manual_job_import(path)
        profile = load_candidate_profile(profile_path)
    except (ValidationError, YamlFileError) as exc:
        _print_load_error("Job scoring failed", exc)
        raise typer.Exit(1) from exc

    scored_jobs = unique_scored_jobs(score_jobs(profile, manual_import.jobs))
    _print_scored_jobs_table(scored_jobs, title="Job Match Summary")
    for scored_job in scored_jobs:
        _print_scored_job_details(scored_job)

    console.print(f"[green]Scored {len(scored_jobs)} job(s).[/green]")


@notion_app.command("check")
def notion_check() -> None:
    """Verify the Notion Applications data source connection and schema."""
    settings = Settings()

    if not settings.notion_api_key:
        console.print("[red]Missing NOTION_API_KEY in .env[/red]")
        raise typer.Exit(1)

    if not settings.notion_applications_data_source_id:
        console.print("[red]Missing NOTION_APPLICATIONS_DATA_SOURCE_ID in .env[/red]")
        raise typer.Exit(1)

    try:
        with NotionClient(
            api_key=settings.notion_api_key,
            api_version=settings.notion_api_version,
        ) as client:
            data_source = client.retrieve_data_source(settings.notion_applications_data_source_id)
            query_result = client.query_data_source(
                settings.notion_applications_data_source_id,
                {"page_size": 1},
            )
    except ImportError:
        console.print("[red]Missing dependency: httpx[/red]")
        console.print("Run: uv sync --extra dev")
        raise typer.Exit(1) from None
    except Exception as exc:
        console.print("[red]Notion check failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc

    problems = validate_applications_data_source(data_source)
    if problems:
        console.print("[yellow]Connected to Notion, but the Applications schema needs fixes:[/yellow]")
        for problem in problems:
            console.print(f"- {problem}")
        raise typer.Exit(1)

    title = data_source.get("name") or "Applications"
    result_count = len(query_result.get("results", []))
    console.print("[green]Notion connection OK[/green]")
    console.print(f"Data source: {title}")
    console.print(f"Readable sample rows: {result_count}")


@notion_app.command("add-submission-fields")
def notion_add_submission_fields() -> None:
    """Add the three delivery audit columns to the configured Applications data source."""
    settings = Settings()
    if not settings.notion_api_key or not settings.notion_applications_data_source_id:
        console.print("[red]Notion configuration is required.[/red]")
        raise typer.Exit(1)
    try:
        with NotionClient(settings.notion_api_key, settings.notion_api_version) as notion_client:
            data_source = notion_client.retrieve_data_source(settings.notion_applications_data_source_id)
            definitions = missing_submission_property_definitions(data_source)
            if definitions:
                notion_client.update_data_source_properties(
                    settings.notion_applications_data_source_id, definitions
                )
            updated = notion_client.retrieve_data_source(settings.notion_applications_data_source_id)
            problems = validate_submission_data_source(updated)
            if problems:
                raise ValueError("; ".join(problems))
    except Exception as exc:
        console.print("[red]Notion submission-field setup failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc
    if definitions:
        console.print(f"[green]Added {len(definitions)} Notion submission field(s).[/green]")
    else:
        console.print("[green]Notion submission fields already exist.[/green]")


@telegram_app.command("check")
def telegram_check() -> None:
    """Verify Telegram bot configuration and connectivity."""
    settings = Settings()

    if not settings.telegram_bot_token:
        console.print("[red]Missing TELEGRAM_BOT_TOKEN in .env[/red]")
        raise typer.Exit(1)

    try:
        with TelegramClient(settings.telegram_bot_token) as client:
            bot_info = client.get_me().get("result", {})
    except ImportError:
        console.print("[red]Missing dependency: httpx[/red]")
        console.print("Run: uv sync --extra dev")
        raise typer.Exit(1) from None
    except Exception as exc:
        console.print("[red]Telegram check failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc

    console.print("[green]Telegram connection OK[/green]")
    console.print(f"Bot username: @{bot_info.get('username', 'unknown')}")
    console.print(
        f"Allowed user IDs: {', '.join(str(user_id) for user_id in sorted(settings.telegram_allowed_user_id_set)) or 'none configured'}"
    )


@telegram_app.command("send-digest")
def telegram_send_digest(
    chat_id: Annotated[int, typer.Option("--chat-id", help="Telegram chat ID to send the digest to.")],
    status: Annotated[
        ApplicationStatus,
        typer.Option("--status", help="Only send jobs currently in this status."),
    ] = ApplicationStatus.FOUND,
    limit: Annotated[int, typer.Option("--limit", min=1, max=25, help="Maximum jobs to send.")] = 10,
) -> None:
    """Send scored job summaries from Notion to Telegram and advance found jobs to digest_sent."""
    settings = Settings()

    if not settings.telegram_bot_token:
        console.print("[red]Missing TELEGRAM_BOT_TOKEN in .env[/red]")
        raise typer.Exit(1)
    if not settings.notion_api_key:
        console.print("[red]Missing NOTION_API_KEY in .env[/red]")
        raise typer.Exit(1)
    if not settings.notion_applications_data_source_id:
        console.print("[red]Missing NOTION_APPLICATIONS_DATA_SOURCE_ID in .env[/red]")
        raise typer.Exit(1)

    try:
        with NotionClient(
            api_key=settings.notion_api_key,
            api_version=settings.notion_api_version,
        ) as notion_client, TelegramClient(settings.telegram_bot_token) as telegram_client:
            jobs = list_jobs_by_status(
                notion_client,
                settings.notion_applications_data_source_id,
                status,
                page_size=limit,
            )
            if not jobs:
                console.print(f"[yellow]No jobs found with status {status.value}.[/yellow]")
                return

            sent_messages = send_digest_messages(
                telegram_client,
                notion_client,
                chat_id,
                jobs,
            )
    except Exception as exc:
        console.print("[red]Telegram digest send failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc

    console.print(
        f"[green]Sent {len(sent_messages)} digest message(s) to chat {chat_id}.[/green]"
    )


@telegram_app.command("peek-updates")
def telegram_peek_updates(
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=20, help="Maximum updates to print."),
    ] = 10,
) -> None:
    """Show recent Telegram updates without changing Notion or storing offsets."""
    settings = Settings()

    if not settings.telegram_bot_token:
        console.print("[red]Missing TELEGRAM_BOT_TOKEN in .env[/red]")
        raise typer.Exit(1)

    try:
        with TelegramClient(settings.telegram_bot_token) as telegram_client:
            updates = telegram_client.get_updates(timeout=0)
    except Exception as exc:
        console.print("[red]Telegram update peek failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc

    if not updates:
        console.print("[yellow]No pending Telegram updates.[/yellow]")
        return

    for update in updates[:limit]:
        callback_query = update.get("callback_query")
        message = update.get("message")
        if isinstance(callback_query, dict):
            from_user = callback_query.get("from", {})
            console.print(
                f"update_id={update.get('update_id')} callback from user_id={from_user.get('id')} "
                f"data={callback_query.get('data')}"
            )
            continue

        if isinstance(message, dict):
            from_user = message.get("from", {})
            console.print(
                f"update_id={update.get('update_id')} message from user_id={from_user.get('id')} "
                f"text={message.get('text')!r}"
            )
            continue

        console.print(f"update_id={update.get('update_id')} unsupported update shape")


@telegram_app.command("poll-once")
def telegram_poll_once(
    timeout_seconds: Annotated[
        int,
        typer.Option("--timeout-seconds", min=0, max=60, help="Long-poll timeout in seconds."),
    ] = 0,
) -> None:
    """Poll Telegram once, process approval callbacks, and store the last update offset."""
    settings = Settings()

    if not settings.telegram_bot_token:
        console.print("[red]Missing TELEGRAM_BOT_TOKEN in .env[/red]")
        raise typer.Exit(1)
    if not settings.notion_api_key:
        console.print("[red]Missing NOTION_API_KEY in .env[/red]")
        raise typer.Exit(1)
    if not settings.notion_applications_data_source_id:
        console.print("[red]Missing NOTION_APPLICATIONS_DATA_SOURCE_ID in .env[/red]")
        raise typer.Exit(1)
    if not settings.telegram_allowed_user_id_set:
        console.print("[red]Missing TELEGRAM_ALLOWED_USER_IDS in .env[/red]")
        raise typer.Exit(1)

    last_update_id = _load_last_telegram_update_id(TELEGRAM_OFFSET_PATH)
    offset = last_update_id + 1 if last_update_id is not None else None

    try:
        with TelegramClient(settings.telegram_bot_token) as telegram_client, NotionClient(
            api_key=settings.notion_api_key,
            api_version=settings.notion_api_version,
        ) as notion_client:
            def deliver_reviewed_email(page_id: str, review_id: str) -> None:
                page = notion_client.retrieve_page(page_id)
                application = application_digest_item_from_page(page)
                if application.status != ApplicationStatus.APPROVED_TO_SUBMIT:
                    raise ValueError("Email delivery requires approved_to_submit status")
                review = load_email_review(page_id, review_id)
                if review.notion_page_id != page_id:
                    raise ValueError("Reviewed email does not belong to this application")
                job = job_from_application_page(page)
                manifest = load_manifest(settings.cv_archive_root, job.company, page_id)
                verify_manifest(manifest, review.draft_id)
                schema_problems = validate_submission_data_source(
                    notion_client.retrieve_data_source(settings.notion_applications_data_source_id)
                )
                if schema_problems:
                    raise ValueError("Notion submission fields are missing: " + "; ".join(schema_problems))
                message_id = send_email(
                    review.recipient,
                    review.subject,
                    review.body,
                    Path(review.attachment_path),
                )
                notion_client.record_submission(
                    page_id,
                    "email",
                    review.contact_source,
                    f"Gmail message {message_id}; recipient {review.recipient}; "
                    f"CV draft {review.draft_id}; reviewed email {review.review_id}",
                )

            def confirm_whatsapp_handoff(page_id: str, handoff_id: str) -> None:
                page = notion_client.retrieve_page(page_id)
                application = application_digest_item_from_page(page)
                if application.status != ApplicationStatus.APPROVED_TO_SUBMIT:
                    raise ValueError("WhatsApp confirmation requires approved_to_submit status")
                handoff = load_whatsapp_handoff(page_id, handoff_id)
                job = job_from_application_page(page)
                manifest = load_manifest(settings.cv_archive_root, job.company, page_id)
                verify_manifest(manifest, handoff.draft_id)
                schema_problems = validate_submission_data_source(
                    notion_client.retrieve_data_source(settings.notion_applications_data_source_id)
                )
                if schema_problems:
                    raise ValueError("Notion submission fields are missing: " + "; ".join(schema_problems))
                notion_client.record_submission(
                    page_id,
                    "whatsapp",
                    handoff.contact_source,
                    f"User confirmed WhatsApp delivery to {handoff.recipient}; "
                    f"CV draft {handoff.draft_id}; handoff {handoff.handoff_id}",
                )

            updates = telegram_client.get_updates(offset=offset, timeout=timeout_seconds)
            processed_actions = [
                action
                for update in updates
                if (action := process_telegram_update(
                    telegram_client,
                    notion_client,
                    update,
                    settings.telegram_allowed_user_id_set,
                    settings.cv_archive_root,
                    deliver_reviewed_email,
                    confirm_whatsapp_handoff,
                ))
            ]
    except Exception as exc:
        console.print("[red]Telegram polling failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc

    if updates:
        _store_last_telegram_update_id(TELEGRAM_OFFSET_PATH, max(update["update_id"] for update in updates))

    console.print(
        f"[green]Processed {len(processed_actions)} approval action(s) from {len(updates)} update(s).[/green]"
    )


@cv_app.command("draft")
def cv_draft(
    page_id: Annotated[str, typer.Argument(help="Notion page ID for an approved job.")],
    evidence_path: CareerEvidencePathOption = DEFAULT_CAREER_EVIDENCE_PATH,
    style_path: CvStylePathOption = DEFAULT_CV_STYLE_PATH,
    chat_id: Annotated[
        int | None,
        typer.Option("--chat-id", help="Telegram chat ID to receive the draft files."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview an evidence-only draft without API calls or writes."),
    ] = False,
    revision_feedback: Annotated[str | None, typer.Option(hidden=True)] = None,
) -> None:
    """Create a CV draft only for a job approved for tailoring."""
    settings = Settings()
    try:
        evidence = load_career_evidence(evidence_path)
        style = load_cv_style(style_path)
    except (ValidationError, YamlFileError) as exc:
        _print_load_error("CV source validation failed", exc)
        raise typer.Exit(1) from exc

    if not settings.notion_api_key or not settings.notion_applications_data_source_id:
        console.print("[red]Notion configuration is required for CV drafting.[/red]")
        raise typer.Exit(1)

    try:
        with NotionClient(
            api_key=settings.notion_api_key,
            api_version=settings.notion_api_version,
        ) as notion_client:
            page = notion_client.retrieve_page(page_id)
            application = application_digest_item_from_page(page)
            job = job_from_application_page(page)
            valid_statuses = {ApplicationStatus.APPROVED_TO_TAILOR}
            if revision_feedback:
                valid_statuses.add(ApplicationStatus.REVISION_REQUESTED)
            if application.status not in valid_statuses:
                raise ValueError(
                    "CV drafting requires approved_to_tailor or a revision request, "
                    f"not {application.status.value}"
                )

            if dry_run:
                draft = build_evidence_only_draft(evidence)
                validate_tailored_cv_against_evidence(draft, evidence, style)
                console.print("[green]Evidence-only draft preview is valid.[/green]")
                console.print(f"Company: {job.company}")
                console.print(f"Role: {job.title}")
                console.print(f"Experience entries: {len(draft.experience)}")
                console.print(f"Project entries: {len(draft.projects)}")
                return

            if not settings.openai_api_key:
                raise ValueError("Missing OPENAI_API_KEY in .env. Use --dry-run to validate locally.")
            if settings.cv_archive_root == Path("applications"):
                raise ValueError("Set CV_ARCHIVE_ROOT in .env to a private folder outside the repository.")
            if chat_id is None:
                raise ValueError("A Telegram --chat-id is required to send the CV draft for approval.")
            if chat_id not in settings.telegram_allowed_user_id_set:
                raise ValueError("The CV approval chat ID must be configured in TELEGRAM_ALLOWED_USER_IDS.")
            if not settings.telegram_bot_token:
                raise ValueError("Missing TELEGRAM_BOT_TOKEN in .env")

            draft = generate_tailored_cv(
                evidence,
                job,
                style,
                settings.openai_api_key,
                settings.openai_model_high_quality,
                revision_feedback,
            )
            if application.status == ApplicationStatus.REVISION_REQUESTED:
                notion_client.update_application_status(page_id, ApplicationStatus.APPROVED_TO_TAILOR)
            directory = next_draft_directory(settings.cv_archive_root, job.company, page_id)
            docx_path = directory / "cv_draft.docx"
            pdf_path = directory / "cv_draft.pdf"
            create_cv_docx(evidence, draft, docx_path, style)
            convert_docx_to_pdf(docx_path, pdf_path)
            manifest = write_manifest(directory, page_id, job.company, job.title, docx_path, pdf_path)

            with TelegramClient(settings.telegram_bot_token) as telegram_client:
                telegram_client.send_document(
                    chat_id,
                    docx_path,
                    f"CV draft for {job.company} - {job.title} (editable DOCX).",
                )
                telegram_client.send_document(
                    chat_id,
                    pdf_path,
                    f"Review this exact CV draft for {job.company} - {job.title}.",
                    reply_markup=build_cv_draft_reply_markup(page_id, manifest.draft_id),
                )

            notion_client.update_application_status(page_id, ApplicationStatus.CV_DRAFTED)
    except typer.Exit:
        raise
    except Exception as exc:
        console.print("[red]CV draft failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc

    console.print(f"[green]Created CV draft:[/green] {docx_path}")
    console.print(f"[green]Created PDF review copy:[/green] {pdf_path}")
    console.print(f"Draft ID: {manifest.draft_id}")


@cv_app.command("revise")
def cv_revise(
    page_id: Annotated[str, typer.Argument(help="Notion page ID with saved revision feedback.")],
    evidence_path: CareerEvidencePathOption = DEFAULT_CAREER_EVIDENCE_PATH,
    style_path: CvStylePathOption = DEFAULT_CV_STYLE_PATH,
    chat_id: Annotated[int, typer.Option("--chat-id", help="Telegram chat for the new draft.")] = 0,
) -> None:
    """Create the next immutable CV version from Telegram revision feedback."""
    try:
        feedback = load_revision_feedback(page_id)
    except ValueError as exc:
        console.print(f"[red]CV revision failed[/red]\n{exc}")
        raise typer.Exit(1) from exc
    if chat_id <= 0:
        raise typer.BadParameter("--chat-id must be a positive Telegram chat ID")
    cv_draft(page_id, evidence_path, style_path, chat_id, False, feedback)


@gmail_app.command("connect")
def gmail_connect() -> None:
    """Authorize Gmail once with the minimum gmail.send permission."""
    settings = Settings()
    path = settings.gmail_oauth_client_secret_path
    if path is None:
        console.print("[red]Missing GMAIL_OAUTH_CLIENT_SECRET_PATH in .env[/red]")
        raise typer.Exit(1)
    if not path.is_file():
        console.print(f"[red]Gmail OAuth client-secret file not found:[/red] {path}")
        raise typer.Exit(1)
    try:
        connect_gmail(path)
    except Exception as exc:
        console.print("[red]Gmail connection failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc
    console.print("[green]Gmail send permission is connected in macOS Keychain.[/green]")


@gmail_app.command("check")
def gmail_check() -> None:
    """Check whether a local Gmail OAuth refresh token exists in macOS Keychain."""
    if gmail_connected():
        console.print("[green]Gmail is connected.[/green]")
    else:
        console.print("[yellow]Gmail is not connected.[/yellow]")


@contacts_app.command("discover")
def contacts_discover(
    page_id: Annotated[str, typer.Argument(help="Notion page ID for an approved job.")],
    official_url: Annotated[
        str | None,
        typer.Option("--official-url", help="Official company careers/contact URL to inspect explicitly."),
    ] = None,
) -> None:
    """Record contacts found in the job post and an explicitly supplied official URL."""
    settings = Settings()
    if not settings.notion_api_key:
        console.print("[red]Missing NOTION_API_KEY in .env[/red]")
        raise typer.Exit(1)
    try:
        with NotionClient(settings.notion_api_key, settings.notion_api_version) as notion_client:
            job = job_from_application_page(notion_client.retrieve_page(page_id))
        contacts = discover_job_post_contacts(job)
        if official_url:
            import httpx

            response = httpx.get(official_url, timeout=20.0, follow_redirects=True)
            response.raise_for_status()
            contacts.extend(discover_official_page_contacts(response.text))
        contacts = list(dict.fromkeys(contacts))
        save_contacts(page_id, contacts)
    except Exception as exc:
        console.print("[red]Contact discovery failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc
    if not contacts:
        console.print("[yellow]No verified email or WhatsApp contact was found.[/yellow]")
        return
    for contact in contacts:
        console.print(f"[green]{contact.channel}[/green] {contact.value} ({contact.source})")


@contacts_app.command("review-email")
def contacts_review_email(
    page_id: Annotated[str, typer.Argument(help="Notion page ID in approved_to_submit state.")],
    recipient: Annotated[str, typer.Option("--recipient", help="Verified recipient email address.")],
    chat_id: Annotated[int, typer.Option("--chat-id", help="Telegram chat ID for the final email review.")],
) -> None:
    """Send a Telegram card showing the exact email and approved CV before delivery."""
    settings = Settings()
    if not all((settings.notion_api_key, settings.telegram_bot_token)):
        console.print("[red]Notion and Telegram configuration are required.[/red]")
        raise typer.Exit(1)
    try:
        contact = require_verified_contact(page_id, "email", recipient)
        evidence = load_career_evidence()
        with NotionClient(settings.notion_api_key, settings.notion_api_version) as notion_client:
            page = notion_client.retrieve_page(page_id)
            application = application_digest_item_from_page(page)
            if application.status != ApplicationStatus.APPROVED_TO_SUBMIT:
                raise ValueError("Email review requires approved_to_submit status")
            job = job_from_application_page(page)
        review = build_email_review(
            settings.cv_archive_root, job.company, page_id, job, evidence, contact.value, contact.source
        )
        save_email_review(review)
        text = (
            f"Email review for {job.company} - {job.title}\n"
            f"To: {review.recipient}\nSource: {review.contact_source}\n"
            f"Subject: {review.subject}\n\n{review.body}\n\n"
            f"Attachment: {Path(review.attachment_path).name} (CV {review.draft_id})"
        )
        with TelegramClient(settings.telegram_bot_token) as telegram_client:
            telegram_client.send_message(chat_id, text)
            telegram_client.send_document(
                chat_id,
                Path(review.attachment_path),
                f"Approved CV attachment for {job.company} - CV {review.draft_id}.",
            )
            telegram_client.send_message(
                chat_id,
                "The attached CV and the exact email above are ready. Send only if both look right.",
                reply_markup=build_email_review_reply_markup(page_id, review.review_id),
            )
    except Exception as exc:
        console.print("[red]Email review failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc
    console.print(f"[green]Sent email review {review.review_id} to Telegram.[/green]")


@contacts_app.command("whatsapp-handoff")
def contacts_whatsapp_handoff(
    page_id: Annotated[str, typer.Argument(help="Notion page ID in approved_to_submit state.")],
    phone: Annotated[str, typer.Option("--phone", help="Verified public WhatsApp number.")],
    chat_id: Annotated[int, typer.Option("--chat-id", help="Telegram chat ID for confirmation.")],
) -> None:
    """Open a prepared WhatsApp Web chat; only the user can attach and send the CV."""
    settings = Settings()
    if not all((settings.notion_api_key, settings.telegram_bot_token)):
        console.print("[red]Notion and Telegram configuration are required.[/red]")
        raise typer.Exit(1)
    try:
        contact = require_verified_contact(page_id, "whatsapp", phone)
        evidence = load_career_evidence()
        with NotionClient(settings.notion_api_key, settings.notion_api_version) as notion_client:
            page = notion_client.retrieve_page(page_id)
            application = application_digest_item_from_page(page)
            if application.status != ApplicationStatus.APPROVED_TO_SUBMIT:
                raise ValueError("WhatsApp handoff requires approved_to_submit status")
            job = job_from_application_page(page)
        handoff = build_whatsapp_handoff(
            settings.cv_archive_root, job.company, page_id, job, evidence, contact.value, contact.source
        )
        save_whatsapp_handoff(handoff)
        import webbrowser

        webbrowser.open(handoff_url(handoff), new=1)
        with TelegramClient(settings.telegram_bot_token) as telegram_client:
            telegram_client.send_document(
                chat_id,
                Path(handoff.attachment_path),
                f"Attach this PDF in the prepared WhatsApp chat for {job.company}.",
            )
            telegram_client.send_message(
                chat_id,
                f"WhatsApp handoff prepared for {handoff.recipient} from {handoff.contact_source}. "
                "The agent did not send anything. Confirm only after you attach the PDF and send it.",
                reply_markup=build_whatsapp_handoff_reply_markup(page_id, handoff.handoff_id),
            )
    except Exception as exc:
        console.print("[red]WhatsApp handoff failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc
    console.print("[green]Prepared WhatsApp Web and Telegram confirmation.[/green]")


@apply_app.command("prepare")
def apply_prepare(
    page_id: Annotated[str, typer.Argument(help="Notion page ID in approved_to_submit state.")],
    wait_for_human_seconds: Annotated[
        int,
        typer.Option("--wait-for-human-seconds", min=0, max=1800, help="Keep the visible browser open for remote CAPTCHA completion."),
    ] = 600,
) -> None:
    """Fill a visible portal form with safe fields and the exact approved CV; do not submit."""
    settings = Settings()
    if not settings.notion_api_key:
        console.print("[red]Missing NOTION_API_KEY in .env[/red]")
        raise typer.Exit(1)
    try:
        evidence = load_career_evidence()
        with NotionClient(settings.notion_api_key, settings.notion_api_version) as notion_client:
            page = notion_client.retrieve_page(page_id)
            application = application_digest_item_from_page(page)
            if application.status not in {
                ApplicationStatus.APPROVED_TO_SUBMIT,
                ApplicationStatus.SUBMISSION_PREPARED,
            }:
                raise ValueError("Browser preparation requires approved_to_submit or submission_prepared status")
            job = job_from_application_page(page)
            if application.status == ApplicationStatus.APPROVED_TO_SUBMIT:
                notion_client.update_application_status(page_id, ApplicationStatus.SUBMISSION_PREPARED)
            manifest = build_email_review(
                settings.cv_archive_root, job.company, page_id, job, evidence,
                evidence.identity.email, "approved_cv_manifest"
            )
            result = prepare_visible_submission(
                job.source_url,
                settings.browser_profile_root,
                evidence,
                Path(manifest.attachment_path),
                lambda: _notify_human_verification(
                    notion_client, settings, page_id, job.company, job.title
                ),
                wait_for_human_seconds,
            )
            notion_client.update_application_status(page_id, ApplicationStatus(result.state))
    except Exception as exc:
        console.print("[red]Browser preparation failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc
    console.print(f"[green]{result.message}[/green]")
    console.print(f"Fields: {', '.join(result.filled_fields) or 'none'}")


@apply_app.command("resume")
def apply_resume(
    page_id: Annotated[str, typer.Argument(help="Notion page ID after manual human verification.")],
) -> None:
    """Submit an approved prepared portal only when its confirmation page is visible."""
    settings = Settings()
    if not settings.notion_api_key:
        console.print("[red]Missing NOTION_API_KEY in .env[/red]")
        raise typer.Exit(1)
    try:
        evidence = load_career_evidence()
        with NotionClient(settings.notion_api_key, settings.notion_api_version) as notion_client:
            page = notion_client.retrieve_page(page_id)
            application = application_digest_item_from_page(page)
            if application.status not in {
                ApplicationStatus.AWAITING_HUMAN_VERIFICATION,
                ApplicationStatus.SUBMISSION_PREPARED,
            }:
                raise ValueError("Resume is only available after browser preparation or human verification")
            if application.status == ApplicationStatus.AWAITING_HUMAN_VERIFICATION:
                notion_client.update_application_status(page_id, ApplicationStatus.SUBMISSION_PREPARED)
            job = job_from_application_page(page)
            manifest = build_email_review(
                settings.cv_archive_root, job.company, page_id, job, evidence,
                evidence.identity.email, "approved_cv_manifest"
            )
            schema_problems = validate_submission_data_source(
                notion_client.retrieve_data_source(settings.notion_applications_data_source_id)
            )
            if schema_problems:
                raise ValueError("Notion submission fields are missing: " + "; ".join(schema_problems))
            result = submit_visible_submission(
                job.source_url,
                settings.browser_profile_root,
                evidence,
                Path(manifest.attachment_path),
                lambda: _notify_human_verification(
                    notion_client, settings, page_id, job.company, job.title
                ),
            )
            if result.state == "submitted":
                notion_client.record_submission(
                    page_id,
                    "portal",
                    "job_post",
                    f"Portal confirmation detected; CV draft {manifest.draft_id}; URL {job.source_url}",
                )
            elif result.state == "awaiting_human_verification":
                pass
            else:
                notion_client.update_application_status(page_id, ApplicationStatus.SUBMISSION_PREPARED)
    except Exception as exc:
        console.print("[red]Application resume failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc
    console.print(f"[green]{result.message}[/green]")


@cv_app.command("evidence-check")
def cv_evidence_check(
    evidence_path: CareerEvidencePathOption = DEFAULT_CAREER_EVIDENCE_PATH,
    style_path: CvStylePathOption = DEFAULT_CV_STYLE_PATH,
) -> None:
    """Validate the private career evidence ledger and CV style without external calls."""
    try:
        evidence = load_career_evidence(evidence_path)
        style = load_cv_style(style_path)
    except (ValidationError, YamlFileError) as exc:
        _print_load_error("CV source validation failed", exc)
        raise typer.Exit(1) from exc

    console.print("[green]Career evidence OK[/green]")
    console.print(f"Experience entries: {len(evidence.experience)}")
    console.print(f"Project entries: {len(evidence.projects)}")
    console.print(f"Evidence IDs: {len(evidence.evidence_ids())}")
    console.print(
        f"CV style OK: headline {'enabled' if style.include_headline else 'disabled'}, "
        f"raw URLs {'visible' if style.show_raw_urls else 'hidden'}"
    )


def _notify_human_verification(
    notion_client: NotionClient,
    settings: Settings,
    page_id: str,
    company: str,
    title: str,
) -> None:
    """Persist the pause before sending an optional remote-control alert."""
    notion_client.update_application_status(page_id, ApplicationStatus.AWAITING_HUMAN_VERIFICATION)
    if not settings.telegram_bot_token or not settings.telegram_allowed_user_id_set:
        return
    text = (
        f"Human verification is waiting for {company} - {title}. "
        f"{settings.remote_desktop_instructions} Then rerun: "
        f"backend-scout apply resume {page_id}"
    )
    with TelegramClient(settings.telegram_bot_token) as telegram_client:
        for chat_id in settings.telegram_allowed_user_id_set:
            telegram_client.send_message(chat_id, text)


def _print_jobs_table(jobs: list[Job], title: str) -> None:
    table = Table(title=title)
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Location")
    table.add_column("Remote")
    table.add_column("Score")
    for job in jobs:
        table.add_row(
            job.company,
            job.title,
            job.location or "-",
            job.remote_policy or "-",
            str(job.match_score) if job.match_score is not None else "-",
        )
    console.print(table)


def _print_scored_jobs_table(scored_jobs: list[ScoredJob], title: str) -> None:
    table = Table(title=title)
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Score")
    table.add_column("Action")
    table.add_column("Salary")
    table.add_column("Location")
    table.add_column("Top Strength")
    table.add_column("Top Concern")

    for scored_job in scored_jobs:
        result = scored_job.result
        table.add_row(
            scored_job.job.company,
            scored_job.job.title,
            str(result.match_score),
            result.recommended_action.value,
            result.salary_assessment.value,
            result.location_assessment.value,
            result.strengths[0] if result.strengths else "-",
            result.concerns[0] if result.concerns else "-",
        )

    console.print(table)


def _print_scored_job_details(scored_job: ScoredJob) -> None:
    job = scored_job.job
    result = scored_job.result

    console.print("")
    console.print(f"[bold]{job.company} - {job.title}[/bold]")
    console.print(f"Score: {result.match_score} | Action: {result.recommended_action.value}")
    console.print(f"Salary: {result.salary_assessment.value} | Location: {result.location_assessment.value}")
    console.print(f"Summary: {result.reason_summary}")

    if result.matched_skills:
        console.print(f"Matched skills: {', '.join(result.matched_skills)}")
    if result.missing_skills:
        console.print(f"Missing skills: {', '.join(result.missing_skills)}")

    _print_bullets("Strengths", result.strengths)
    _print_bullets("Concerns", result.concerns)

    console.print("Breakdown:")
    for item in result.score_breakdown:
        console.print(
            f"- {item.component}: {item.points_awarded}/{item.points_max} - {item.reason}"
        )


def _print_bullets(title: str, items: list[str]) -> None:
    if not items:
        return

    console.print(f"{title}:")
    for item in items:
        console.print(f"- {item}")


def _print_load_error(title: str, exc: ValidationError | YamlFileError) -> None:
    console.print(f"[red]{title}[/red]")
    if isinstance(exc, ValidationError):
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "(root)"
            console.print(f"- {location}: {error['msg']}")
        return

    console.print(str(exc))


def _load_last_telegram_update_id(path: Path) -> int | None:
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return None
    return int(content)


def _store_last_telegram_update_id(path: Path, update_id: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(update_id), encoding="utf-8")


app.add_typer(notion_app, name="notion")
app.add_typer(profile_app, name="profile")
app.add_typer(jobs_app, name="jobs")
app.add_typer(telegram_app, name="telegram")
app.add_typer(cv_app, name="cv")
app.add_typer(gmail_app, name="gmail")
app.add_typer(contacts_app, name="contacts")
app.add_typer(apply_app, name="apply")


if __name__ == "__main__":
    app()
