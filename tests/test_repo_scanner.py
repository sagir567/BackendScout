from pathlib import Path

from backend_scout.repo_scanner import (
    GithubRepoSource,
    LocalRepoSource,
    RepoSources,
    format_repo_scan_report,
    scan_local_repository,
    scan_public_github_owner,
    scan_repositories,
)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir(exist_ok=True)
    return path


def test_scan_local_repository_detects_python_fastapi_docker_and_ci(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("from fastapi import FastAPI\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        'dependencies = ["fastapi", "pydantic", "typer", "openai"]\n[project.optional-dependencies]\ndev = ["pytest", "ruff"]',
        encoding="utf-8",
    )
    (repo / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "tests.yml").write_text("name: tests\n", encoding="utf-8")

    proposal = scan_local_repository(LocalRepoSource(path=repo, name="Agent"))

    assert "Python" in proposal.detected_languages
    assert {"FastAPI", "Pydantic", "Typer", "OpenAI API"} <= set(proposal.detected_frameworks)
    assert {"Docker", "pytest", "Ruff", "GitHub Actions", "CI/CD"} <= set(proposal.detected_tools)
    assert "pyproject.toml" in proposal.evidence_files
    assert proposal.proposed_evidence_bullets


def test_scan_local_repository_detects_dotnet_and_database_stack(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "Api").mkdir()
    (repo / "Api" / "Program.cs").write_text("var builder = WebApplication.CreateBuilder(args);", encoding="utf-8")
    (repo / "Api" / "Api.csproj").write_text(
        "<Project Sdk=\"Microsoft.NET.Sdk.Web\"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup>"
        "<ItemGroup><PackageReference Include=\"Microsoft.EntityFrameworkCore\" />"
        "<PackageReference Include=\"Npgsql.EntityFrameworkCore.PostgreSQL\" />"
        "<PackageReference Include=\"xunit\" />"
        "<PackageReference Include=\"FluentAssertions\" />"
        "<PackageReference Include=\"Testcontainers\" /></ItemGroup></Project>",
        encoding="utf-8",
    )

    proposal = scan_local_repository(LocalRepoSource(path=repo, name="PrintIt"))

    assert "C#" in proposal.detected_languages
    assert {"ASP.NET Core", ".NET", "Entity Framework Core"} <= set(proposal.detected_frameworks)
    assert {"PostgreSQL", "xUnit", "FluentAssertions", "Testcontainers"} <= set(proposal.detected_tools)


def test_scan_local_repository_ignores_dependency_directories(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "node_modules" / "fake").mkdir(parents=True)
    (repo / "node_modules" / "fake" / "main.py").write_text("from fastapi import FastAPI", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "main.cpp").write_text("int main() { return 0; }", encoding="utf-8")

    proposal = scan_local_repository(LocalRepoSource(path=repo))

    assert proposal.detected_languages == ["C++"]
    assert "FastAPI" not in proposal.detected_frameworks


def test_scan_public_github_owner_uses_metadata_and_languages() -> None:
    def fetch(url: str):
        if url.endswith("/repos?per_page=60&sort=updated"):
            return [
                {
                    "name": "CPP",
                    "html_url": "https://github.com/sagir567/CPP",
                    "languages_url": "https://api.github.com/repos/sagir567/CPP/languages",
                    "description": "c++ from zero to hero",
                    "language": "C++",
                    "topics": [],
                    "fork": False,
                }
            ]
        return {"C++": 10000, "C": 200}

    proposals = scan_public_github_owner(GithubRepoSource(owner="sagir567"), 60, fetch)

    assert len(proposals) == 1
    assert proposals[0].source_name == "CPP"
    assert proposals[0].detected_languages[:2] == ["C++", "C"]
    assert proposals[0].confidence == "metadata"


def test_scan_repositories_collects_skips_and_formats_report(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "main.cpp").write_text("int main() { return 0; }", encoding="utf-8")
    sources = RepoSources(
        local_repositories=[
            LocalRepoSource(path=repo, name="CPP Practice"),
            LocalRepoSource(path=tmp_path / "missing", name="Missing"),
        ]
    )

    report = scan_repositories(sources)
    text = format_repo_scan_report(report)

    assert len(report.proposals) == 1
    assert report.skipped_sources
    assert "CPP Practice" in text
