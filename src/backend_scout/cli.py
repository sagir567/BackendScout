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
from backend_scout.job_imports import load_manual_job_import
from backend_scout.matcher import ScoredJob, score_jobs
from backend_scout.models import ApplicationStatus, Job
from backend_scout.notion import NotionClient, validate_applications_data_source
from backend_scout.yaml_files import YamlFileError

app = typer.Typer(help="BackendScout job-search agent CLI.")
notion_app = typer.Typer(help="Notion integration commands.")
profile_app = typer.Typer(help="Candidate profile commands.")
jobs_app = typer.Typer(help="Manual job import commands.")
app.add_typer(notion_app, name="notion")
app.add_typer(profile_app, name="profile")
app.add_typer(jobs_app, name="jobs")
console = Console()

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
    except (ValidationError, YamlFileError) as exc:
        _print_load_error("Manual job import failed", exc)
        raise typer.Exit(1) from exc

    _print_jobs_table(manual_import.jobs, "Manual Job Import Preview")

    if not write_notion:
        console.print("[yellow]Preview only.[/yellow] Re-run with --write-notion to create Notion rows.")
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

            created = [
                client.create_job_page(settings.notion_applications_data_source_id, job)
                for job in manual_import.jobs
            ]
    except typer.Exit:
        raise
    except Exception as exc:
        console.print("[red]Notion import failed[/red]")
        console.print(str(exc))
        raise typer.Exit(1) from exc

    console.print(f"[green]Created {len(created)} Notion row(s).[/green]")


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

    scored_jobs = score_jobs(profile, manual_import.jobs)
    _print_scored_jobs_table(scored_jobs)
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


def _print_scored_jobs_table(scored_jobs: list[ScoredJob]) -> None:
    table = Table(title="Job Match Summary")
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


if __name__ == "__main__":
    app()
