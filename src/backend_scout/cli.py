from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import httpx
import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from backend_scout.application_answers import load_application_form_answers
from backend_scout.candidate_profile import (
    DEFAULT_PROFILE_PATH,
    candidate_profile_warnings,
    load_candidate_profile,
)
from backend_scout.career_evidence import DEFAULT_CAREER_EVIDENCE_PATH, load_career_evidence
from backend_scout.collectors import collect_public_jobs, is_israel_or_remote
from backend_scout.config import Settings, TrackerName
from backend_scout.contact_records import require_verified_contact, save_contacts
from backend_scout.contacts import discover_job_post_contacts, discover_official_page_contacts
from backend_scout.cv_artifacts import (
    draft_directory,
    load_manifest,
    next_draft_directory,
    verify_manifest,
    write_manifest,
)
from backend_scout.cv_coverage import CV_EVIDENCE_COVERAGE_TARGET, assess_cv_evidence_coverage
from backend_scout.cv_documents import render_checked_cv_artifacts
from backend_scout.cv_style import DEFAULT_CV_STYLE_PATH, load_cv_style
from backend_scout.cv_tailoring import (
    build_evidence_only_draft,
    generate_tailored_cv,
    validate_tailored_cv_against_evidence,
)
from backend_scout.gmail import connect_gmail, gmail_connected, list_recent_messages, send_email
from backend_scout.job_imports import (
    job_with_match_result,
    load_manual_job_import,
    score_manual_job_import,
    unique_scored_jobs,
)
from backend_scout.launchd import (
    LaunchdService,
    install_launchd_service,
    launchd_service_installed,
    selected_services,
    uninstall_launchd_service,
)
from backend_scout.mailbox import (
    DEFAULT_MAILBOX_QUERY,
    classify_gmail_message,
    format_mailbox_audit_record,
    format_mailbox_digest,
    match_message_to_application,
)
from backend_scout.matcher import ScoredJob, score_jobs
from backend_scout.models import (
    ApplicationDigestItem,
    ApplicationStatus,
    CvEvidenceCoverage,
    Job,
    can_transition_application_status,
)
from backend_scout.notion import (
    NotionClient,
    application_digest_item_from_page,
    job_from_application_page,
    list_jobs_by_status,
    missing_submission_property_definitions,
    missing_workflow_status_property_definition,
    missing_workflow_statuses,
    upsert_job_page,
    validate_applications_data_source,
    validate_submission_data_source,
)
from backend_scout.outreach import build_email_review, load_email_review, save_email_review
from backend_scout.portal_authorizations import (
    authorize_portal_submit,
    consume_portal_submit_authorization,
    create_portal_submit_authorization,
    find_pending_portal_submit_authorization,
)
from backend_scout.repo_scanner import (
    DEFAULT_REPO_SOURCES_PATH,
    format_repo_scan_report,
    load_repo_sources,
    scan_repositories,
)
from backend_scout.revisions import load_revision_feedback
from backend_scout.scouting_preferences import (
    DEFAULT_SCOUTING_PREFERENCES_PATH,
    ScoutingPreferences,
    load_scouting_preferences,
    public_collection_digest_candidate,
)
from backend_scout.submission import prepare_visible_submission, submit_visible_submission
from backend_scout.tailoring_notes import load_tailoring_note
from backend_scout.targets import DEFAULT_TARGET_COMPANIES_PATH, load_target_companies
from backend_scout.task_queue import (
    DEFAULT_TASK_QUEUE_PATH,
    QueuedTaskKind,
    QueuedTaskStatus,
    enqueue_task,
    list_tasks,
    mark_task_status,
)
from backend_scout.telegram import (
    TelegramClient,
    build_cv_draft_reply_markup,
    build_email_review_reply_markup,
    build_portal_submit_reply_markup,
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
collect_app = typer.Typer(help="Collect public ATS jobs for Israel and approved remote work.")
repos_app = typer.Typer(help="Scan Git repositories and propose evidence updates.")
tasks_app = typer.Typer(help="Run queued Telegram-first work items.")
system_app = typer.Typer(help="Manage local BackendScout runtime helpers.")
launchd_app = typer.Typer(help="Install or inspect macOS launchd agents.")
console = Console()
TELEGRAM_OFFSET_PATH = Path("data/telegram/last_update_id.txt")
DEFAULT_LAUNCHD_AGENT_DIR = Path.home() / "Library" / "LaunchAgents"

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
TargetCompaniesPathOption = Annotated[
    Path,
    typer.Option("--targets-path", help="Private target-company YAML path."),
]
ScoutingPreferencesPathOption = Annotated[
    Path,
    typer.Option("--preferences-path", help="Optional private scouting-preferences YAML path."),
]
RepoSourcesPathOption = Annotated[
    Path,
    typer.Option("--path", help="Private repository-source YAML path."),
]
TrackerOption = Annotated[
    TrackerName,
    typer.Option("--tracker", help="Notion tracker to use. Defaults to the isolated test tracker."),
]
LaunchdAgentDirOption = Annotated[
    Path,
    typer.Option("--agent-dir", help="LaunchAgents directory to write plist files into."),
]


def _tracker_data_source_id(settings: Settings, tracker: TrackerName) -> str | None:
    resolver = getattr(settings, "applications_data_source_id_for", None)
    if callable(resolver):
        return resolver(tracker)
    if tracker == TrackerName.PRODUCTION:
        return getattr(settings, "notion_production_applications_data_source_id", None)
    return getattr(settings, "notion_test_applications_data_source_id", None) or getattr(
        settings, "notion_applications_data_source_id", None
    )


def _require_tracker_data_source_id(settings: Settings, tracker: TrackerName) -> str:
    data_source_id = _tracker_data_source_id(settings, tracker)
    if data_source_id:
        return data_source_id
    env_name = (
        "NOTION_PRODUCTION_APPLICATIONS_DATA_SOURCE_ID"
        if tracker == TrackerName.PRODUCTION
        else "NOTION_TEST_APPLICATIONS_DATA_SOURCE_ID"
    )
    raise ValueError(f"Missing {env_name} in .env")


def _require_page_in_tracker(page: dict[str, object], data_source_id: str) -> None:
    parent = page.get("parent")
    if not isinstance(parent, dict):
        return
    page_data_source_id = parent.get("data_source_id")
    if page_data_source_id is not None and page_data_source_id != data_source_id:
        raise ValueError("Notion page belongs to a different tracker")


@app.command()
def status() -> None:
    """Show the current local configuration."""
    settings = Settings()

    table = Table(title="BackendScout")
    table.add_column("Setting")
    table.add_column("Value")
    table.add_row("Notion API version", settings.notion_api_version)
    table.add_row(
        "Notion test data source",
        "configured" if _tracker_data_source_id(settings, TrackerName.TEST) else "missing",
    )
    table.add_row(
        "Notion production data source",
        "configured" if _tracker_data_source_id(settings, TrackerName.PRODUCTION) else "missing",
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
    tracker: TrackerOption = TrackerName.TEST,
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
    try:
        data_source_id = _require_tracker_data_source_id(settings, tracker)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    try:
        with NotionClient(
            api_key=settings.notion_api_key,
            api_version=settings.notion_api_version,
        ) as client:
            data_source = client.retrieve_data_source(data_source_id)
            problems = validate_applications_data_source(data_source)
            if problems:
                console.print("[yellow]Connected to Notion, but the Applications schema needs fixes:[/yellow]")
                for problem in problems:
                    console.print(f"- {problem}")
                raise typer.Exit(1)

            actions = [
                upsert_job_page(
                    client,
                    data_source_id,
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


@jobs_app.command("promote")
def jobs_promote(
    test_page_id: Annotated[str, typer.Argument(help="Notion page ID from the test tracker.")],
    application_url: Annotated[
        str,
        typer.Option("--application-url", help="Verified official company application URL."),
    ],
    chat_id: Annotated[
        int | None,
        typer.Option("--chat-id", help="Telegram chat ID; defaults to the configured daily chat."),
    ] = None,
    profile_path: ScoreProfilePathOption = DEFAULT_PROFILE_PATH,
) -> None:
    """Copy one reviewed test job into production as a fresh approval workflow."""
    settings = Settings()
    if not settings.notion_api_key or not settings.telegram_bot_token:
        console.print("[red]Notion and Telegram configuration are required for promotion.[/red]")
        raise typer.Exit(1)
    try:
        test_data_source_id = _require_tracker_data_source_id(settings, TrackerName.TEST)
        production_data_source_id = _require_tracker_data_source_id(settings, TrackerName.PRODUCTION)
        profile = load_candidate_profile(profile_path)
    except (ValueError, ValidationError, YamlFileError) as exc:
        _print_load_error("Job promotion failed", exc)
        raise typer.Exit(1) from exc

    try:
        with NotionClient(settings.notion_api_key, settings.notion_api_version) as notion_client:
            test_page = notion_client.retrieve_page(test_page_id)
            _require_page_in_tracker(test_page, test_data_source_id)
            test_job = job_from_application_page(test_page)
            promoted_job = test_job.model_copy(update={"application_url": application_url})
            scored_job = score_jobs(profile, [promoted_job])[0]
            production_job = job_with_match_result(scored_job.job, scored_job)
            production_schema = notion_client.retrieve_data_source(production_data_source_id)
            problems = validate_applications_data_source(production_schema)
            if problems:
                raise ValueError("Production tracker schema needs fixes: " + "; ".join(problems))
            if notion_client.find_job_page(production_data_source_id, production_job):
                raise ValueError("A matching production application already exists; it was not changed")
            production_page = notion_client.create_job_page(production_data_source_id, production_job)
            item = application_digest_item_from_page(production_page)
            resolved_chat_id = _resolve_daily_chat_id(settings, chat_id)
            with TelegramClient(settings.telegram_bot_token) as telegram_client:
                send_digest_messages(
                    telegram_client,
                    notion_client,
                    resolved_chat_id,
                    [item],
                    TrackerName.PRODUCTION,
                )
    except Exception as exc:
        console.print("[red]Job promotion failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc

    console.print(f"[green]Promoted {production_job.company} - {production_job.title} to production.[/green]")
    console.print(f"Production page ID: {production_page['id']}")


@collect_app.command("validate")
def collect_validate(targets_path: TargetCompaniesPathOption = DEFAULT_TARGET_COMPANIES_PATH) -> None:
    """Validate the private public-ATS target-company list without network calls."""
    try:
        targets = load_target_companies(targets_path)
    except (ValidationError, YamlFileError) as exc:
        _print_load_error("Target-company validation failed", exc)
        raise typer.Exit(1) from exc
    enabled = [target for target in targets.companies if target.enabled]
    console.print(f"[green]Target companies OK:[/green] {len(enabled)} enabled of {len(targets.companies)} total.")
    for target in enabled:
        console.print(f"- {target.name} ({target.provider.value})")


@collect_app.command("preferences-check")
def collect_preferences_check(
    preferences_path: ScoutingPreferencesPathOption = DEFAULT_SCOUTING_PREFERENCES_PATH,
) -> None:
    """Validate optional private scouting digest preferences."""
    try:
        preferences = load_scouting_preferences(preferences_path)
    except (ValidationError, YamlFileError) as exc:
        _print_load_error("Scouting preference validation failed", exc)
        raise typer.Exit(1) from exc

    source = str(preferences_path) if preferences_path.exists() else "built-in defaults"
    console.print(f"[green]Scouting preferences OK:[/green] {source}")
    console.print(f"Preferred title keywords: {', '.join(preferences.preferred_title_keywords) or 'none'}")
    console.print(f"Maybe title keywords: {', '.join(preferences.maybe_title_keywords) or 'none'}")
    console.print(f"Excluded title keywords: {', '.join(preferences.excluded_title_keywords) or 'none'}")
    console.print(f"Minimum match score: {preferences.minimum_match_score}")
    console.print(f"Minimum role relevance: {preferences.minimum_role_relevance_points}/25")


@collect_app.command("run")
def collect_run(
    targets_path: TargetCompaniesPathOption = DEFAULT_TARGET_COMPANIES_PATH,
    preferences_path: ScoutingPreferencesPathOption = DEFAULT_SCOUTING_PREFERENCES_PATH,
    profile_path: ScoreProfilePathOption = DEFAULT_PROFILE_PATH,
    write_notion: Annotated[
        bool, typer.Option("--write-notion", help="Sync filtered jobs to Notion; default is preview only.")
    ] = False,
    send_digest: Annotated[
        bool, typer.Option("--send-digest", help="Send Telegram messages for newly created jobs after syncing.")
    ] = False,
    chat_id: Annotated[
        int | None, typer.Option("--chat-id", help="Telegram chat ID; defaults to configured daily chat.")
    ] = None,
    tracker: TrackerOption = TrackerName.TEST,
) -> None:
    """Collect, filter, score, and optionally sync public Israel-relevant ATS jobs."""
    if send_digest and not write_notion:
        raise typer.BadParameter("--send-digest requires --write-notion")
    try:
        targets = load_target_companies(targets_path)
        preferences = load_scouting_preferences(preferences_path)
        profile = load_candidate_profile(profile_path)
    except (ValidationError, YamlFileError) as exc:
        _print_load_error("Public collection setup failed", exc)
        raise typer.Exit(1) from exc

    collected: list[Job] = []
    failures: list[str] = []
    for target in (item for item in targets.companies if item.enabled):
        try:
            collected.extend(collect_public_jobs(target))
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            failures.append(f"{target.name}: {exc}")
    israel_relevant = [job for job in collected if is_israel_or_remote(job)]
    scored_jobs = unique_scored_jobs(score_jobs(profile, israel_relevant))
    shortlist = [scored_job for scored_job in scored_jobs if _is_public_collection_shortlist(scored_job, preferences)]
    _print_scored_jobs_table(shortlist, title="Public ATS Collection Shortlist")
    console.print(
        f"Collected {len(collected)} public job(s); Israel/remote filter kept {len(israel_relevant)}; "
        f"unique scored jobs: {len(scored_jobs)}; apply/maybe shortlist: {len(shortlist)}."
    )
    for failure in failures:
        console.print(f"[yellow]Collector warning:[/yellow] {failure}")
    if not write_notion:
        console.print("[yellow]Preview only. Re-run with --write-notion to sync new jobs.[/yellow]")
        return

    settings = Settings()
    if not settings.notion_api_key:
        console.print("[red]Notion configuration is required for syncing.[/red]")
        raise typer.Exit(1)
    try:
        data_source_id = _require_tracker_data_source_id(settings, tracker)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    created_pages: list[dict[str, object]] = []
    try:
        with NotionClient(settings.notion_api_key, settings.notion_api_version) as notion_client:
            schema_problems = validate_applications_data_source(
                notion_client.retrieve_data_source(data_source_id)
            )
            if schema_problems:
                raise ValueError("; ".join(schema_problems))
            for scored_job in shortlist:
                action, page = upsert_job_page(
                    notion_client,
                    data_source_id,
                    job_with_match_result(scored_job.job, scored_job),
                )
                if action == "created":
                    created_pages.append(page)
            if send_digest and created_pages:
                resolved_chat_id = _resolve_daily_chat_id(settings, chat_id)
                if not settings.telegram_bot_token:
                    raise ValueError("TELEGRAM_BOT_TOKEN is required for --send-digest")
                items = [application_digest_item_from_page(page) for page in created_pages]
                with TelegramClient(settings.telegram_bot_token) as telegram_client:
                    send_digest_messages(telegram_client, notion_client, resolved_chat_id, items, tracker)
    except Exception as exc:
        console.print("[red]Public collection sync failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc
    console.print(f"[green]Synced {len(shortlist)} shortlisted job(s); created {len(created_pages)} new Notion row(s).[/green]")
    if send_digest:
        console.print(f"[green]Sent {len(created_pages)} new-job digest message(s).[/green]")


def _is_public_collection_shortlist(scored_job: ScoredJob, preferences: ScoutingPreferences) -> bool:
    return public_collection_digest_candidate(scored_job, preferences)


@repos_app.command("validate")
def repos_validate(path: RepoSourcesPathOption = DEFAULT_REPO_SOURCES_PATH) -> None:
    """Validate private local and public GitHub repository scan sources."""
    try:
        sources = load_repo_sources(path)
    except (ValidationError, YamlFileError) as exc:
        _print_load_error("Repository-source validation failed", exc)
        raise typer.Exit(1) from exc
    enabled_local = [source for source in sources.local_repositories if source.enabled]
    enabled_github = [source for source in sources.github_repositories if source.enabled]
    console.print(
        f"[green]Repository sources OK:[/green] {len(enabled_local)} local, "
        f"{len(enabled_github)} public GitHub owner(s)."
    )


@repos_app.command("scan")
def repos_scan(
    path: RepoSourcesPathOption = DEFAULT_REPO_SOURCES_PATH,
    output: Annotated[
        Path,
        typer.Option("--output", help="Ignored JSON report path for evidence proposals."),
    ] = Path("data/repo_scans/latest.json"),
    send_telegram: Annotated[
        bool,
        typer.Option("--send-telegram", help="Send the proposal summary to Telegram for review."),
    ] = False,
    chat_id: Annotated[
        int | None,
        typer.Option("--chat-id", help="Telegram chat ID; defaults to configured daily chat."),
    ] = None,
    tracker: TrackerOption = TrackerName.TEST,
) -> None:
    """Scan configured Git repositories and propose evidence updates without applying them."""
    try:
        sources = load_repo_sources(path)
        report = scan_repositories(sources)
    except (ValidationError, YamlFileError) as exc:
        _print_load_error("Repository scan failed", exc)
        raise typer.Exit(1) from exc
    except (OSError, ValueError) as exc:
        console.print("[red]Repository scan failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    summary = format_repo_scan_report(report)
    console.print(summary)
    console.print(f"[green]Saved proposal report:[/green] {output}")
    if send_telegram:
        settings = Settings()
        if not settings.telegram_bot_token:
            console.print("[red]Missing TELEGRAM_BOT_TOKEN in .env[/red]")
            raise typer.Exit(1)
        resolved_chat_id = _resolve_daily_chat_id(settings, chat_id)
        with TelegramClient(settings.telegram_bot_token) as telegram_client:
            telegram_client.send_message(
                resolved_chat_id,
                summary + "\n\nThese are proposals only. Approve evidence before adding it to the CV ledger.",
            )
        console.print(f"[green]Sent repository scan summary to chat {resolved_chat_id}.[/green]")


@launchd_app.command("install")
def launchd_install(
    service: Annotated[LaunchdService, typer.Argument(help="Service to install: daily, telegram, mailbox, worker, all.")],
    agent_dir: LaunchdAgentDirOption = DEFAULT_LAUNCHD_AGENT_DIR,
    load: Annotated[bool, typer.Option("--load", help="Load the agent with launchctl after writing it.")] = False,
    replace: Annotated[
        bool,
        typer.Option("--replace", help="Unload an existing agent before loading the rendered plist."),
    ] = False,
) -> None:
    """Render BackendScout launchd templates into the user's LaunchAgents directory."""
    for selected in selected_services(service):
        path = install_launchd_service(Path.cwd(), agent_dir, selected, load=load, replace=replace)
        console.print(f"[green]Installed {selected.value}:[/green] {path}")
    if not load:
        console.print("[yellow]Plists were written but not loaded. Re-run with --load to start them.[/yellow]")


@launchd_app.command("uninstall")
def launchd_uninstall(
    service: Annotated[LaunchdService, typer.Argument(help="Service to uninstall: daily, telegram, mailbox, worker, all.")],
    agent_dir: LaunchdAgentDirOption = DEFAULT_LAUNCHD_AGENT_DIR,
    unload: Annotated[bool, typer.Option("--unload", help="Unload the agent with launchctl before deleting it.")] = False,
) -> None:
    """Remove generated BackendScout launchd plist files."""
    for selected in selected_services(service):
        path = uninstall_launchd_service(agent_dir, selected, unload=unload)
        console.print(f"[green]Removed {selected.value}:[/green] {path}")


@launchd_app.command("status")
def launchd_status(agent_dir: LaunchdAgentDirOption = DEFAULT_LAUNCHD_AGENT_DIR) -> None:
    """Show whether BackendScout launchd plist files are installed."""
    for selected in selected_services(LaunchdService.ALL):
        state = "installed" if launchd_service_installed(agent_dir, selected) else "missing"
        console.print(f"{selected.value}: {state}")


@tasks_app.command("list")
def tasks_list(
    queue_path: Annotated[
        Path,
        typer.Option("--queue-path", help="Ignored local task queue JSON path."),
    ] = DEFAULT_TASK_QUEUE_PATH,
) -> None:
    """List queued and recently processed Telegram-first tasks."""
    tasks = list_tasks(queue_path)
    table = Table(title="BackendScout Tasks")
    table.add_column("ID")
    table.add_column("Kind")
    table.add_column("Tracker")
    table.add_column("Status")
    table.add_column("Error")
    for task in tasks:
        table.add_row(
            task.task_id,
            task.kind.value,
            task.tracker.value,
            task.status.value,
            task.last_error or "-",
        )
    console.print(table)


@tasks_app.command("worker-once")
def tasks_worker_once(
    queue_path: Annotated[
        Path,
        typer.Option("--queue-path", help="Ignored local task queue JSON path."),
    ] = DEFAULT_TASK_QUEUE_PATH,
) -> None:
    """Process one queued task and exit."""
    queued = list_tasks(queue_path, QueuedTaskStatus.QUEUED)
    if not queued:
        console.print("[yellow]No queued tasks.[/yellow]")
        return
    task = queued[0]
    mark_task_status(task.task_id, QueuedTaskStatus.RUNNING, path=queue_path)
    try:
        if task.kind == QueuedTaskKind.SCOUT_TODAY:
            collect_run(
                write_notion=True,
                send_digest=True,
                chat_id=task.payload.get("chat_id"),
                tracker=task.tracker,
            )
        elif task.kind == QueuedTaskKind.CV_DRAFT:
            cv_draft(
                _payload_string(task.payload, "page_id"),
                chat_id=_payload_int(task.payload, "chat_id"),
                tracker=task.tracker,
            )
        elif task.kind == QueuedTaskKind.PORTAL_PREPARE:
            apply_prepare(
                _payload_string(task.payload, "page_id"),
                tracker=task.tracker,
            )
        else:
            raise ValueError(f"No worker is implemented yet for {task.kind.value}")
    except Exception as exc:
        mark_task_status(task.task_id, QueuedTaskStatus.FAILED, str(exc), queue_path)
        console.print(f"[red]Task {task.task_id} failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    mark_task_status(task.task_id, QueuedTaskStatus.DONE, path=queue_path)
    console.print(f"[green]Task {task.task_id} completed.[/green]")


@notion_app.command("check")
def notion_check(tracker: TrackerOption = TrackerName.TEST) -> None:
    """Verify the Notion Applications data source connection and schema."""
    settings = Settings()

    if not settings.notion_api_key:
        console.print("[red]Missing NOTION_API_KEY in .env[/red]")
        raise typer.Exit(1)

    try:
        data_source_id = _require_tracker_data_source_id(settings, tracker)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    try:
        with NotionClient(
            api_key=settings.notion_api_key,
            api_version=settings.notion_api_version,
        ) as client:
            data_source = client.retrieve_data_source(data_source_id)
            query_result = client.query_data_source(
                data_source_id,
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
def notion_add_submission_fields(tracker: TrackerOption = TrackerName.TEST) -> None:
    """Add the three delivery audit columns to the configured Applications data source."""
    settings = Settings()
    if not settings.notion_api_key:
        console.print("[red]Notion configuration is required.[/red]")
        raise typer.Exit(1)
    try:
        data_source_id = _require_tracker_data_source_id(settings, tracker)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    try:
        with NotionClient(settings.notion_api_key, settings.notion_api_version) as notion_client:
            data_source = notion_client.retrieve_data_source(data_source_id)
            definitions = missing_submission_property_definitions(data_source)
            definitions.update(missing_workflow_status_property_definition(data_source))
            if definitions:
                notion_client.update_data_source_properties(
                    data_source_id, definitions
                )
            updated = notion_client.retrieve_data_source(data_source_id)
            problems = validate_submission_data_source(updated)
            problems.extend(
                f"Missing workflow Status option: {status}"
                for status in missing_workflow_statuses(updated)
            )
            if problems:
                raise ValueError("; ".join(problems))
    except Exception as exc:
        console.print("[red]Notion submission-field setup failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc
    if definitions:
        console.print(f"[green]Added {len(definitions)} Notion workflow schema update(s).[/green]")
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
    tracker: TrackerOption = TrackerName.TEST,
) -> None:
    """Send scored job summaries from Notion to Telegram and advance found jobs to digest_sent."""
    settings = Settings()

    if not settings.telegram_bot_token:
        console.print("[red]Missing TELEGRAM_BOT_TOKEN in .env[/red]")
        raise typer.Exit(1)
    if not settings.notion_api_key:
        console.print("[red]Missing NOTION_API_KEY in .env[/red]")
        raise typer.Exit(1)
    try:
        data_source_id = _require_tracker_data_source_id(settings, tracker)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    try:
        with NotionClient(
            api_key=settings.notion_api_key,
            api_version=settings.notion_api_version,
        ) as notion_client, TelegramClient(settings.telegram_bot_token) as telegram_client:
            jobs = list_jobs_by_status(
                notion_client,
                data_source_id,
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
                tracker,
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
    tracker: TrackerOption = TrackerName.TEST,
) -> None:
    """Poll Telegram once, process approval callbacks, and store the last update offset."""
    settings = Settings()

    if not settings.telegram_bot_token:
        console.print("[red]Missing TELEGRAM_BOT_TOKEN in .env[/red]")
        raise typer.Exit(1)
    if not settings.notion_api_key:
        console.print("[red]Missing NOTION_API_KEY in .env[/red]")
        raise typer.Exit(1)
    try:
        data_source_id = _require_tracker_data_source_id(settings, tracker)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
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
                    notion_client.retrieve_data_source(data_source_id)
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
                    notion_client.retrieve_data_source(data_source_id)
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

            def authorize_portal_submission(page_id: str, authorization_id: str) -> None:
                page = notion_client.retrieve_page(page_id)
                application = application_digest_item_from_page(page)
                if application.status != ApplicationStatus.SUBMISSION_PREPARED:
                    raise ValueError("Portal submission requires browser preparation first")
                job = job_from_application_page(page)
                application_url = job.application_url or job.source_url
                if not application_url:
                    raise ValueError("Portal submission requires an application URL")
                manifest = load_manifest(settings.cv_archive_root, job.company, page_id)
                verify_manifest(manifest, manifest.draft_id)
                authorize_portal_submit(
                    page_id,
                    authorization_id,
                    tracker,
                    application_url,
                    manifest,
                )

            def enqueue_long_task(
                kind: QueuedTaskKind,
                task_tracker: TrackerName,
                payload: dict[str, object],
            ) -> str:
                return enqueue_task(kind, task_tracker, payload).task_id

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
                    tracker,
                    data_source_id,
                    portal_submission_authorization_handler=authorize_portal_submission,
                    task_enqueue_handler=enqueue_long_task,
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


@telegram_app.command("listen")
def telegram_listen(
    timeout_seconds: Annotated[
        int,
        typer.Option("--timeout-seconds", min=1, max=60, help="Long-poll timeout in seconds."),
    ] = 30,
    tracker: TrackerOption = TrackerName.PRODUCTION,
) -> None:
    """Continuously process authorized Telegram workflow actions on one tracker."""
    console.print(
        f"[green]Listening for authorized Telegram actions on the {tracker.value} tracker. "
        "Press Ctrl-C to stop.[/green]"
    )
    try:
        while True:
            telegram_poll_once(timeout_seconds=timeout_seconds, tracker=tracker)
    except KeyboardInterrupt:
        console.print("[yellow]Telegram listener stopped.[/yellow]")


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
    tracker: TrackerOption = TrackerName.TEST,
) -> None:
    """Create a CV draft only for a job approved for tailoring."""
    settings = Settings()
    try:
        evidence = load_career_evidence(evidence_path)
        style = load_cv_style(style_path)
    except (ValidationError, YamlFileError) as exc:
        _print_load_error("CV source validation failed", exc)
        raise typer.Exit(1) from exc

    if not settings.notion_api_key:
        console.print("[red]Notion configuration is required for CV drafting.[/red]")
        raise typer.Exit(1)
    try:
        data_source_id = _require_tracker_data_source_id(settings, tracker)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    try:
        with NotionClient(
            api_key=settings.notion_api_key,
            api_version=settings.notion_api_version,
        ) as notion_client:
            page = notion_client.retrieve_page(page_id)
            _require_page_in_tracker(page, data_source_id)
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

            coverage = assess_cv_evidence_coverage(evidence, job)
            _print_cv_evidence_coverage(job, coverage)
            if coverage.coverage_score < CV_EVIDENCE_COVERAGE_TARGET:
                raise ValueError(
                    "CV drafting is paused because factual evidence coverage is below the required "
                    f"{CV_EVIDENCE_COVERAGE_TARGET}/100 target. Confirm only truthful missing evidence "
                    "or complete a real project, then update config/career_evidence.yaml and retry."
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
                (note.text if (note := load_tailoring_note(page_id, tracker.value)) else None),
            )
            if application.status == ApplicationStatus.REVISION_REQUESTED:
                notion_client.update_application_status(page_id, ApplicationStatus.APPROVED_TO_TAILOR)
            directory = next_draft_directory(settings.cv_archive_root, job.company, page_id)
            docx_path = directory / "cv_draft.docx"
            pdf_path = directory / "cv_draft.pdf"
            layout_metrics = render_checked_cv_artifacts(evidence, draft, docx_path, pdf_path, style)
            manifest = write_manifest(
                directory,
                page_id,
                job.company,
                job.title,
                docx_path,
                pdf_path,
                tracker.value,
                note.text if note else None,
            )

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
                    reply_markup=build_cv_draft_reply_markup(page_id, manifest.draft_id, tracker),
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
    console.print(f"Page fill: {layout_metrics.content_fill_ratio:.0%} on one page")
    console.print(f"Draft ID: {manifest.draft_id}")


@cv_app.command("coverage")
def cv_coverage(
    page_id: Annotated[str, typer.Argument(help="Notion page ID for the job to assess.")],
    evidence_path: CareerEvidencePathOption = DEFAULT_CAREER_EVIDENCE_PATH,
    tracker: TrackerOption = TrackerName.TEST,
) -> None:
    """Show factual CV evidence coverage before tailoring a tracked job."""
    settings = Settings()
    try:
        evidence = load_career_evidence(evidence_path)
    except (ValidationError, YamlFileError) as exc:
        _print_load_error("Career evidence validation failed", exc)
        raise typer.Exit(1) from exc
    if not settings.notion_api_key:
        console.print("[red]Notion configuration is required for coverage assessment.[/red]")
        raise typer.Exit(1)
    try:
        data_source_id = _require_tracker_data_source_id(settings, tracker)
        with NotionClient(
            api_key=settings.notion_api_key,
            api_version=settings.notion_api_version,
        ) as notion_client:
            page = notion_client.retrieve_page(page_id)
            _require_page_in_tracker(page, data_source_id)
            job = job_from_application_page(page)
    except Exception as exc:
        console.print("[red]CV evidence coverage failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc

    _print_cv_evidence_coverage(job, assess_cv_evidence_coverage(evidence, job))


@cv_app.command("revise")
def cv_revise(
    page_id: Annotated[str, typer.Argument(help="Notion page ID with saved revision feedback.")],
    evidence_path: CareerEvidencePathOption = DEFAULT_CAREER_EVIDENCE_PATH,
    style_path: CvStylePathOption = DEFAULT_CV_STYLE_PATH,
    chat_id: Annotated[int, typer.Option("--chat-id", help="Telegram chat for the new draft.")] = 0,
    tracker: TrackerOption = TrackerName.TEST,
) -> None:
    """Create the next immutable CV version from Telegram revision feedback."""
    try:
        feedback = load_revision_feedback(page_id)
    except ValueError as exc:
        console.print(f"[red]CV revision failed[/red]\n{exc}")
        raise typer.Exit(1) from exc
    if chat_id <= 0:
        raise typer.BadParameter("--chat-id must be a positive Telegram chat ID")
    cv_draft(page_id, evidence_path, style_path, chat_id, False, feedback, tracker)


@gmail_app.command("connect")
def gmail_connect() -> None:
    """Authorize Gmail once with send and readonly mailbox permissions."""
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
    console.print("[green]Gmail send/read permissions are connected in macOS Keychain.[/green]")


@gmail_app.command("check")
def gmail_check() -> None:
    """Check whether a local Gmail OAuth refresh token exists in macOS Keychain."""
    if gmail_connected():
        console.print("[green]Gmail is connected.[/green]")
    else:
        console.print("[yellow]Gmail is not connected.[/yellow]")


@gmail_app.command("watch-once")
def gmail_watch_once(
    query: Annotated[
        str,
        typer.Option("--query", help="Gmail search query for mailbox status scanning."),
    ] = DEFAULT_MAILBOX_QUERY,
    max_results: Annotated[
        int,
        typer.Option("--max-results", min=1, max=50, help="Maximum Gmail messages to inspect."),
    ] = 25,
    write_notion: Annotated[
        bool,
        typer.Option("--write-notion", help="Update matched application statuses in Notion."),
    ] = False,
    send_digest: Annotated[
        bool,
        typer.Option("--send-digest", help="Send relevant mailbox updates to Telegram."),
    ] = False,
    chat_id: Annotated[
        int | None,
        typer.Option("--chat-id", help="Telegram chat ID; defaults to configured daily chat."),
    ] = None,
    tracker: TrackerOption = TrackerName.PRODUCTION,
) -> None:
    """Inspect Gmail for employer replies and optionally update the tracker."""
    try:
        messages = list_recent_messages(query, max_results)
    except Exception as exc:
        console.print("[red]Gmail mailbox scan failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc
    updates = [(message, classify_gmail_message(message), None) for message in messages]
    updates = [item for item in updates if item[1].status is not None or item[1].confidence == "ambiguous"]
    matched_updates = updates

    settings = Settings()
    if write_notion or send_digest:
        if not settings.notion_api_key:
            console.print("[red]Missing NOTION_API_KEY in .env[/red]")
            raise typer.Exit(1)
        try:
            data_source_id = _require_tracker_data_source_id(settings, tracker)
            with NotionClient(settings.notion_api_key, settings.notion_api_version) as notion_client:
                pages = notion_client.query_data_source(data_source_id, {"page_size": 100}).get("results", [])
                applications = [
                    application_digest_item_from_page(page)
                    for page in pages
                    if isinstance(page, dict)
                ]
                matched_updates = [
                    (message, classification, match_message_to_application(message, applications))
                    for message, classification, _application in updates
                ]
                if write_notion:
                    for message, classification, application in matched_updates:
                        if application is None or classification.status is None:
                            continue
                        if can_transition_application_status(application.status, classification.status):
                            notion_client.update_application_status(
                                application.notion_page_id,
                                classification.status,
                            )
                            notion_client.append_submission_record(
                                application.notion_page_id,
                                format_mailbox_audit_record(message, classification),
                            )
        except Exception as exc:
            console.print("[red]Mailbox tracker update failed[/red]")
            console.print(str(exc))
            raise typer.Exit(1) from exc

    _print_mailbox_updates(matched_updates)
    if send_digest and matched_updates:
        if not settings.telegram_bot_token:
            console.print("[red]Missing TELEGRAM_BOT_TOKEN in .env[/red]")
            raise typer.Exit(1)
        resolved_chat_id = _resolve_daily_chat_id(settings, chat_id)
        with TelegramClient(settings.telegram_bot_token) as telegram_client:
            telegram_client.send_message(resolved_chat_id, format_mailbox_digest(matched_updates))
        console.print(f"[green]Sent mailbox digest to chat {resolved_chat_id}.[/green]")


@contacts_app.command("discover")
def contacts_discover(
    page_id: Annotated[str, typer.Argument(help="Notion page ID for an approved job.")],
    official_url: Annotated[
        str | None,
        typer.Option("--official-url", help="Official company careers/contact URL to inspect explicitly."),
    ] = None,
    tracker: TrackerOption = TrackerName.TEST,
) -> None:
    """Record contacts found in the job post and an explicitly supplied official URL."""
    settings = Settings()
    if not settings.notion_api_key:
        console.print("[red]Missing NOTION_API_KEY in .env[/red]")
        raise typer.Exit(1)
    data_source_id = _require_tracker_data_source_id(settings, tracker)
    try:
        with NotionClient(settings.notion_api_key, settings.notion_api_version) as notion_client:
            page = notion_client.retrieve_page(page_id)
            _require_page_in_tracker(page, data_source_id)
            job = job_from_application_page(page)
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
    tracker: TrackerOption = TrackerName.TEST,
) -> None:
    """Send a Telegram card showing the exact email and approved CV before delivery."""
    settings = Settings()
    if not all((settings.notion_api_key, settings.telegram_bot_token)):
        console.print("[red]Notion and Telegram configuration are required.[/red]")
        raise typer.Exit(1)
    data_source_id = _require_tracker_data_source_id(settings, tracker)
    try:
        contact = require_verified_contact(page_id, "email", recipient)
        evidence = load_career_evidence()
        with NotionClient(settings.notion_api_key, settings.notion_api_version) as notion_client:
            page = notion_client.retrieve_page(page_id)
            _require_page_in_tracker(page, data_source_id)
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
                reply_markup=build_email_review_reply_markup(page_id, review.review_id, tracker),
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
    tracker: TrackerOption = TrackerName.TEST,
) -> None:
    """Open a prepared WhatsApp Web chat; only the user can attach and send the CV."""
    settings = Settings()
    if not all((settings.notion_api_key, settings.telegram_bot_token)):
        console.print("[red]Notion and Telegram configuration are required.[/red]")
        raise typer.Exit(1)
    data_source_id = _require_tracker_data_source_id(settings, tracker)
    try:
        contact = require_verified_contact(page_id, "whatsapp", phone)
        evidence = load_career_evidence()
        with NotionClient(settings.notion_api_key, settings.notion_api_version) as notion_client:
            page = notion_client.retrieve_page(page_id)
            _require_page_in_tracker(page, data_source_id)
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
                reply_markup=build_whatsapp_handoff_reply_markup(page_id, handoff.handoff_id, tracker),
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
    tracker: TrackerOption = TrackerName.TEST,
) -> None:
    """Fill a visible portal form with safe fields and the exact approved CV; do not submit."""
    settings = Settings()
    if not settings.notion_api_key:
        console.print("[red]Missing NOTION_API_KEY in .env[/red]")
        raise typer.Exit(1)
    data_source_id = _require_tracker_data_source_id(settings, tracker)
    try:
        evidence = load_career_evidence()
        with NotionClient(settings.notion_api_key, settings.notion_api_version) as notion_client:
            page = notion_client.retrieve_page(page_id)
            _require_page_in_tracker(page, data_source_id)
            application = application_digest_item_from_page(page)
            if application.status not in {
                ApplicationStatus.APPROVED_TO_SUBMIT,
                ApplicationStatus.SUBMISSION_PREPARED,
            }:
                raise ValueError("Browser preparation requires approved_to_submit or submission_prepared status")
            job = job_from_application_page(page)
            workflow_statuses = missing_workflow_statuses(
                notion_client.retrieve_data_source(data_source_id)
            )
            if workflow_statuses:
                raise ValueError(
                    "Notion Status is missing workflow options: "
                    + ", ".join(workflow_statuses)
                    + ". Run: backend-scout notion add-submission-fields --tracker "
                    + tracker.value
                )
            if application.status == ApplicationStatus.APPROVED_TO_SUBMIT:
                notion_client.update_application_status(page_id, ApplicationStatus.SUBMISSION_PREPARED)
            manifest = build_email_review(
                settings.cv_archive_root, job.company, page_id, job, evidence,
                evidence.identity.email, "approved_cv_manifest"
            )
            result = prepare_visible_submission(
                job.application_url or job.source_url,
                settings.browser_profile_root,
                evidence,
                Path(manifest.attachment_path),
                lambda: _notify_human_verification(
                    notion_client, settings, page_id, job.company, job.title
                ),
                wait_for_human_seconds,
                load_application_form_answers(page_id, tracker),
            )
            if result.resolved_application_url and result.resolved_application_url != job.application_url:
                job = job.model_copy(update={"application_url": result.resolved_application_url})
                notion_client.update_job_page(page_id, job)
            notion_client.update_application_status(page_id, ApplicationStatus(result.state))
    except Exception as exc:
        console.print("[red]Browser preparation failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc
    console.print(f"[green]{result.message}[/green]")
    console.print(f"Fields: {', '.join(result.filled_fields) or 'none'}")
    if result.unresolved_required_fields:
        console.print(
            "[yellow]Required fields still unresolved: "
            + ", ".join(result.unresolved_required_fields)
            + "[/yellow]"
        )


@apply_app.command("request-submit")
def apply_request_submit(
    page_id: Annotated[str, typer.Argument(help="Notion page ID in submission_prepared state.")],
    chat_id: Annotated[
        int | None,
        typer.Option("--chat-id", help="Telegram chat ID; defaults to the configured daily chat."),
    ] = None,
    tracker: TrackerOption = TrackerName.TEST,
) -> None:
    """Send a short-lived Telegram approval for one exact portal submit click."""
    settings = Settings()
    if not settings.notion_api_key or not settings.telegram_bot_token:
        console.print("[red]Notion and Telegram configuration are required.[/red]")
        raise typer.Exit(1)
    data_source_id = _require_tracker_data_source_id(settings, tracker)
    try:
        with NotionClient(settings.notion_api_key, settings.notion_api_version) as notion_client:
            page = notion_client.retrieve_page(page_id)
            _require_page_in_tracker(page, data_source_id)
            application = application_digest_item_from_page(page)
            if application.status != ApplicationStatus.SUBMISSION_PREPARED:
                raise ValueError("Final portal approval requires submission_prepared status")
            job = job_from_application_page(page)
            application_url = job.application_url or job.source_url
            if not application_url:
                raise ValueError("Final portal approval requires an application URL")
            manifest = load_manifest(settings.cv_archive_root, job.company, page_id)
            verify_manifest(manifest, manifest.draft_id)
            authorization = create_portal_submit_authorization(
                page_id, tracker, application_url, manifest
            )
            resolved_chat_id = _resolve_daily_chat_id(settings, chat_id)
            portal_host = urlparse(application_url).netloc or application_url
            message = (
                f"Final portal submission review\n\n"
                f"{job.company} - {job.title}\n"
                f"Portal: {portal_host}\n"
                f"Exact CV draft: {manifest.draft_id}\n"
                "Press Submit now to authorize one browser submit click. "
                "This approval expires in 15 minutes."
            )
            with TelegramClient(settings.telegram_bot_token) as telegram_client:
                telegram_client.send_message(
                    resolved_chat_id,
                    message,
                    reply_markup=build_portal_submit_reply_markup(
                        page_id, authorization.authorization_id, tracker
                    ),
                )
    except Exception as exc:
        console.print("[red]Final portal approval request failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc
    console.print("[green]Sent final portal submission approval to Telegram.[/green]")


@apply_app.command("resume")
def apply_resume(
    page_id: Annotated[str, typer.Argument(help="Notion page ID after manual verification or final Telegram approval.")],
    wait_for_human_seconds: Annotated[
        int,
        typer.Option(
            "--wait-for-human-seconds",
            min=0,
            max=1800,
            help="Keep the visible browser open for remote human verification after a submit attempt.",
        ),
    ] = 600,
    tracker: TrackerOption = TrackerName.TEST,
) -> None:
    """Resume a human-check pause or submit once with a Telegram authorization."""
    settings = Settings()
    if not settings.notion_api_key:
        console.print("[red]Missing NOTION_API_KEY in .env[/red]")
        raise typer.Exit(1)
    data_source_id = _require_tracker_data_source_id(settings, tracker)
    try:
        evidence = load_career_evidence()
        with NotionClient(settings.notion_api_key, settings.notion_api_version) as notion_client:
            page = notion_client.retrieve_page(page_id)
            _require_page_in_tracker(page, data_source_id)
            application = application_digest_item_from_page(page)
            if application.status not in {ApplicationStatus.AWAITING_HUMAN_VERIFICATION, ApplicationStatus.SUBMISSION_PREPARED}:
                raise ValueError("Resume is only available after browser preparation or human verification")
            job = job_from_application_page(page)
            review = build_email_review(
                settings.cv_archive_root, job.company, page_id, job, evidence,
                evidence.identity.email, "approved_cv_manifest"
            )
            manifest = load_manifest(settings.cv_archive_root, job.company, page_id)
            verify_manifest(manifest, manifest.draft_id)
            application_url = job.application_url or job.source_url
            if not application_url:
                raise ValueError("Portal submission requires an application URL")
            if application.status == ApplicationStatus.AWAITING_HUMAN_VERIFICATION:
                result = prepare_visible_submission(
                    application_url,
                    settings.browser_profile_root,
                    evidence,
                    Path(review.attachment_path),
                    lambda: _notify_human_verification(
                        notion_client, settings, page_id, job.company, job.title
                    ),
                    form_answers=load_application_form_answers(page_id, tracker),
                )
                notion_client.update_application_status(page_id, ApplicationStatus(result.state))
                console.print(f"[green]{result.message}[/green]")
                console.print(f"Fields: {', '.join(result.filled_fields) or 'none'}")
                if result.unresolved_required_fields:
                    console.print(
                        "[yellow]Required fields still unresolved: "
                        + ", ".join(result.unresolved_required_fields)
                        + "[/yellow]"
                )
                return
            authorization = find_pending_portal_submit_authorization(
                page_id, tracker, application_url, manifest
            )
            schema_problems = validate_submission_data_source(
                notion_client.retrieve_data_source(data_source_id)
            )
            if schema_problems:
                raise ValueError("Notion submission fields are missing: " + "; ".join(schema_problems))
            proof_path = (
                draft_directory(settings.submission_proof_root, job.company, page_id)
                / f"{manifest.draft_id}-success.png"
            )
            result = submit_visible_submission(
                application_url,
                settings.browser_profile_root,
                evidence,
                Path(review.attachment_path),
                lambda: _notify_human_verification(
                    notion_client, settings, page_id, job.company, job.title
                ),
                lambda: consume_portal_submit_authorization(
                    page_id, authorization.authorization_id, tracker, application_url, manifest
                ),
                load_application_form_answers(page_id, tracker),
                wait_for_human_seconds,
                proof_path,
            )
            if result.state == "submitted":
                submission_record = f"Portal confirmation detected; CV draft {manifest.draft_id}; URL {application_url}"
                if result.screenshot_path and result.screenshot_sha256:
                    submission_record += (
                        f"; proof screenshot {result.screenshot_path}; "
                        f"proof sha256 {result.screenshot_sha256}"
                    )
                notion_client.record_submission(
                    page_id,
                    "portal",
                    "job_post",
                    submission_record,
                )
                if settings.telegram_bot_token and settings.telegram_allowed_user_id_set and result.screenshot_path:
                    with TelegramClient(settings.telegram_bot_token) as telegram_client:
                        for proof_chat_id in sorted(settings.telegram_allowed_user_id_set):
                            telegram_client.send_photo(
                                proof_chat_id,
                                Path(result.screenshot_path),
                                f"Submission proof for {job.company} - {job.title}.",
                            )
            elif result.state == "awaiting_human_verification":
                notion_client.update_application_status(
                    page_id, ApplicationStatus.AWAITING_HUMAN_VERIFICATION
                )
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


def _resolve_daily_chat_id(settings: Settings, explicit_chat_id: int | None) -> int:
    if explicit_chat_id is not None:
        return explicit_chat_id
    if settings.telegram_default_chat_id is not None:
        return settings.telegram_default_chat_id
    allowed = settings.telegram_allowed_user_id_set
    if len(allowed) == 1:
        return next(iter(allowed))
    raise ValueError("Set TELEGRAM_DEFAULT_CHAT_ID or pass --chat-id for the daily digest")


def _payload_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Queued task is missing string payload key: {key}")
    return value


def _payload_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"Queued task is missing integer payload key: {key}")
    return value


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


def _print_cv_evidence_coverage(job: Job, coverage: CvEvidenceCoverage) -> None:
    """Render the factual requirements check used to decide whether CV drafting can start."""
    console.print("")
    console.print(f"[bold]CV evidence coverage: {job.company} - {job.title}[/bold]")
    console.print(
        f"Score: {coverage.coverage_score}/{coverage.target_score} target | {coverage.summary}"
    )
    _print_bullets("Covered requirements", coverage.covered_requirements)
    _print_bullets("Evidence gaps", coverage.missing_requirements)
    _print_bullets("Questions to resolve truthfully", coverage.clarification_questions)


def _print_mailbox_updates(updates: list[tuple[object, object, ApplicationDigestItem | None]]) -> None:
    table = Table(title="Mailbox Application Updates")
    table.add_column("Application")
    table.add_column("Signal")
    table.add_column("Subject")
    table.add_column("Reason")
    for message, classification, application in updates:
        target = f"{application.company} - {application.title}" if application else "unmatched"
        status = classification.status.value if classification.status else "review"
        table.add_row(target, status, message.subject or "-", classification.reason)
    console.print(table)


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
app.add_typer(collect_app, name="collect")
app.add_typer(repos_app, name="repos")
app.add_typer(tasks_app, name="tasks")
system_app.add_typer(launchd_app, name="launchd")
app.add_typer(system_app, name="system")


if __name__ == "__main__":
    app()
