from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from backend_scout.config import Settings
from backend_scout.models import ApplicationStatus
from backend_scout.notion import NotionClient, validate_applications_data_source

app = typer.Typer(help="BackendScout job-search agent CLI.")
notion_app = typer.Typer(help="Notion integration commands.")
app.add_typer(notion_app, name="notion")
console = Console()


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
        console.print("Run: python -m pip install -e \".[dev]\"")
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


if __name__ == "__main__":
    app()
