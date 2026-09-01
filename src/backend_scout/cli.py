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
from backend_scout.config import Settings
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
    list_jobs_by_status,
    upsert_job_page,
    validate_applications_data_source,
)
from backend_scout.telegram import TelegramClient, process_telegram_update, send_digest_messages
from backend_scout.yaml_files import YamlFileError

app = typer.Typer(help="BackendScout job-search agent CLI.")
notion_app = typer.Typer(help="Notion integration commands.")
profile_app = typer.Typer(help="Candidate profile commands.")
jobs_app = typer.Typer(help="Manual job import commands.")
telegram_app = typer.Typer(help="Telegram approval commands.")
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
            updates = telegram_client.get_updates(offset=offset, timeout=timeout_seconds)
            processed_actions = [
                action
                for update in updates
                if (action := process_telegram_update(
                    telegram_client,
                    notion_client,
                    update,
                    settings.telegram_allowed_user_id_set,
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


if __name__ == "__main__":
    app()
