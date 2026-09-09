"""Read-only repository scanner that proposes CV evidence."""

from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend_scout.yaml_files import load_yaml_mapping

DEFAULT_REPO_SOURCES_PATH = Path("config/repo_sources.yaml")

IGNORED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "bin",
    "build",
    "dist",
    "node_modules",
    "obj",
}

LANGUAGE_EXTENSIONS = {
    ".py": "Python",
    ".cs": "C#",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".hh": "C++",
    ".hxx": "C++",
    ".c": "C",
    ".h": "C/C++",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
}

KEY_FILES = {
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "package.json",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "CMakeLists.txt",
}


class LocalRepoSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    path: Path
    enabled: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        if value is None or not isinstance(value, str):
            return value
        return value.strip() or None

    @field_validator("path", mode="before")
    @classmethod
    def expand_path(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return str(Path(value).expanduser())


class GithubRepoSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str
    kind: Literal["user", "org"] = "user"
    enabled: bool = True

    @field_validator("owner", mode="before")
    @classmethod
    def normalize_owner(cls, value: object) -> object:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("owner must not be blank")
        return value.strip().strip("/")


class RepoSources(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_repositories: list[LocalRepoSource] = Field(default_factory=list)
    github_repositories: list[GithubRepoSource] = Field(default_factory=list)
    max_files_per_local_repo: int = Field(default=2500, ge=1, le=20000)
    max_public_repos_per_owner: int = Field(default=60, ge=1, le=100)

    @model_validator(mode="after")
    def require_at_least_one_source(self) -> "RepoSources":
        if not self.local_repositories and not self.github_repositories:
            raise ValueError("at least one repository source is required")
        return self


class RepoEvidenceProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: str
    source_ref: str
    detected_languages: list[str] = Field(default_factory=list)
    detected_frameworks: list[str] = Field(default_factory=list)
    detected_tools: list[str] = Field(default_factory=list)
    evidence_files: list[str] = Field(default_factory=list)
    proposed_skills: list[str] = Field(default_factory=list)
    proposed_evidence_bullets: list[str] = Field(default_factory=list)
    confidence: Literal["observed", "metadata"] = "observed"


class RepoScanReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scanned_at: datetime
    proposals: list[RepoEvidenceProposal] = Field(default_factory=list)
    skipped_sources: list[str] = Field(default_factory=list)


JsonFetcher = Callable[[str], Any]


def load_repo_sources(path: Path = DEFAULT_REPO_SOURCES_PATH) -> RepoSources:
    return RepoSources.model_validate(load_yaml_mapping(path))


def scan_repositories(
    sources: RepoSources,
    fetch_json: JsonFetcher | None = None,
) -> RepoScanReport:
    proposals: list[RepoEvidenceProposal] = []
    skipped: list[str] = []
    for local_source in (source for source in sources.local_repositories if source.enabled):
        try:
            proposals.append(scan_local_repository(local_source, sources.max_files_per_local_repo))
        except (OSError, ValueError) as exc:
            skipped.append(f"{local_source.path}: {exc}")
    for github_source in (source for source in sources.github_repositories if source.enabled):
        try:
            proposals.extend(
                scan_public_github_owner(
                    github_source,
                    sources.max_public_repos_per_owner,
                    fetch_json,
                )
            )
        except (TypeError, ValueError) as exc:
            skipped.append(f"github:{github_source.owner}: {exc}")
    return RepoScanReport(
        scanned_at=datetime.now(UTC),
        proposals=proposals,
        skipped_sources=skipped,
    )


def scan_local_repository(source: LocalRepoSource, max_files: int = 2500) -> RepoEvidenceProposal:
    root = source.path.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("path is not a directory")
    if not (root / ".git").is_dir():
        raise ValueError("path is not a Git repository")

    files = list(_iter_source_files(root, max_files))
    language_counts = Counter(
        language for path in files if (language := LANGUAGE_EXTENSIONS.get(path.suffix.lower()))
    )
    evidence_files = _evidence_files(root, files)
    text = _read_detection_text(root, files)
    frameworks, tools = _detect_frameworks_and_tools(files, text)
    languages = [language for language, _ in language_counts.most_common()]
    proposed_skills = _ordered_unique([*languages, *frameworks, *tools])
    name = source.name or root.name
    bullet = _local_evidence_bullet(name, languages, frameworks, tools)
    return RepoEvidenceProposal(
        source_name=name,
        source_ref=str(root),
        detected_languages=languages,
        detected_frameworks=frameworks,
        detected_tools=tools,
        evidence_files=evidence_files,
        proposed_skills=proposed_skills,
        proposed_evidence_bullets=[bullet] if bullet else [],
    )


def scan_public_github_owner(
    source: GithubRepoSource,
    max_repos: int,
    fetch_json: JsonFetcher | None = None,
) -> list[RepoEvidenceProposal]:
    fetch = fetch_json or _fetch_json
    owner_path = "users" if source.kind == "user" else "orgs"
    repos_url = f"https://api.github.com/{owner_path}/{source.owner}/repos?per_page={max_repos}&sort=updated"
    repos = fetch(repos_url)
    if not isinstance(repos, list):
        raise TypeError("GitHub repository response must be a list")
    proposals: list[RepoEvidenceProposal] = []
    for repo in repos[:max_repos]:
        if not isinstance(repo, dict) or repo.get("fork"):
            continue
        name = _text(repo.get("name"))
        html_url = _text(repo.get("html_url"))
        if not name or not html_url:
            continue
        languages = _github_languages(repo, fetch)
        topics = [topic for topic in repo.get("topics", []) if isinstance(topic, str)]
        frameworks, tools = _detect_github_metadata(repo, topics)
        proposed_skills = _ordered_unique([*languages, *frameworks, *tools])
        bullet = _github_evidence_bullet(name, html_url, languages, frameworks, tools, repo)
        proposals.append(
            RepoEvidenceProposal(
                source_name=name,
                source_ref=html_url,
                detected_languages=languages,
                detected_frameworks=frameworks,
                detected_tools=tools,
                evidence_files=[html_url],
                proposed_skills=proposed_skills,
                proposed_evidence_bullets=[bullet] if bullet else [],
                confidence="metadata",
            )
        )
    return proposals


def format_repo_scan_report(report: RepoScanReport, max_proposals: int = 12) -> str:
    lines = [
        (
            f"Repository evidence scan: {len(report.proposals)} proposal(s), "
            f"{len(report.skipped_sources)} skipped source(s)."
        )
    ]
    for proposal in report.proposals[:max_proposals]:
        lines.append("")
        lines.append(f"{proposal.source_name}")
        if proposal.proposed_skills:
            lines.append(f"Skills: {', '.join(proposal.proposed_skills[:10])}")
        for bullet in proposal.proposed_evidence_bullets[:2]:
            lines.append(f"- {bullet}")
    if len(report.proposals) > max_proposals:
        lines.append(f"\n...and {len(report.proposals) - max_proposals} more proposal(s).")
    if report.skipped_sources:
        lines.append("\nSkipped:")
        lines.extend(f"- {item}" for item in report.skipped_sources[:5])
    return "\n".join(lines)


def _iter_source_files(root: Path, max_files: int):
    count = 0
    for path in root.rglob("*"):
        if count >= max_files:
            break
        if not path.is_file() or _has_ignored_part(path.relative_to(root)):
            continue
        count += 1
        yield path


def _has_ignored_part(path: Path) -> bool:
    return any(part in IGNORED_DIR_NAMES for part in path.parts)


def _evidence_files(root: Path, files: list[Path]) -> list[str]:
    selected: list[str] = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.name in KEY_FILES or relative.startswith(".github/workflows/"):
            selected.append(relative)
    if selected:
        return selected[:12]
    return [path.relative_to(root).as_posix() for path in files[:12]]


def _read_detection_text(root: Path, files: list[Path]) -> str:
    chunks: list[str] = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.name not in KEY_FILES and not relative.startswith(".github/workflows/") and path.suffix not in {
            ".csproj",
            ".fsproj",
            ".sln",
        }:
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="ignore")[:20_000])
        except OSError:
            continue
    return "\n".join(chunks).casefold()


def _detect_frameworks_and_tools(files: list[Path], text: str) -> tuple[list[str], list[str]]:
    names = {path.name.casefold() for path in files}
    relatives = {path.as_posix().casefold() for path in files}
    frameworks: list[str] = []
    tools: list[str] = []
    if "fastapi" in text:
        frameworks.append("FastAPI")
    if "pydantic" in text:
        frameworks.append("Pydantic")
    if "typer" in text:
        frameworks.append("Typer")
    if "openai" in text:
        frameworks.append("OpenAI API")
    if "microsoft.net.sdk.web" in text or "asp.net" in text or "aspnetcore" in text:
        frameworks.append("ASP.NET Core")
    if "targetframework>net8" in text or "targetframework>net9" in text:
        frameworks.append(".NET")
    if "entityframeworkcore" in text:
        frameworks.append("Entity Framework Core")
    if "npgsql" in text or "postgres" in text:
        tools.append("PostgreSQL")
    if "react" in text:
        frameworks.append("React")
    if "vite" in text:
        frameworks.append("Vite")
    if "playwright" in text:
        tools.append("Playwright")
    if "pytest" in text or "pytest" in names:
        tools.append("pytest")
    if "ruff" in text:
        tools.append("Ruff")
    if "xunit" in text:
        tools.append("xUnit")
    if "fluentassertions" in text:
        tools.append("FluentAssertions")
    if "testcontainers" in text:
        tools.append("Testcontainers")
    if "dockerfile" in names:
        tools.append("Docker")
    if {"docker-compose.yml", "docker-compose.yaml"} & names:
        tools.append("Docker Compose")
    if any("/.github/workflows/" in relative or relative.startswith(".github/workflows/") for relative in relatives):
        tools.append("GitHub Actions")
        tools.append("CI/CD")
    if "cmakelists.txt" in names:
        tools.append("CMake")
    return _ordered_unique(frameworks), _ordered_unique(tools)


def _detect_github_metadata(repo: dict[str, Any], topics: list[str]) -> tuple[list[str], list[str]]:
    text = " ".join(
        filter(
            None,
            [
                _text(repo.get("description")),
                _text(repo.get("language")),
                " ".join(topics),
            ],
        )
    ).casefold()
    frameworks: list[str] = []
    tools: list[str] = []
    if ".net" in text or "asp.net" in text or "c#" in text:
        frameworks.append(".NET")
    if "fastapi" in text:
        frameworks.append("FastAPI")
    if "unity" in text:
        frameworks.append("Unity")
    if "docker" in text:
        tools.append("Docker")
    if "ci" in text or "github-actions" in text:
        tools.append("CI/CD")
    return frameworks, tools


def _github_languages(repo: dict[str, Any], fetch: JsonFetcher) -> list[str]:
    language_url = _text(repo.get("languages_url"))
    primary_language = _text(repo.get("language"))
    if not language_url:
        return [primary_language] if primary_language else []
    payload = fetch(language_url)
    if not isinstance(payload, dict):
        return [primary_language] if primary_language else []
    languages = [language for language, _ in sorted(payload.items(), key=lambda item: item[1], reverse=True)]
    return [language for language in languages if isinstance(language, str)] or (
        [primary_language] if primary_language else []
    )


def _local_evidence_bullet(
    name: str,
    languages: list[str],
    frameworks: list[str],
    tools: list[str],
) -> str:
    observed = _ordered_unique([*languages[:3], *frameworks[:4], *tools[:4]])
    if not observed:
        return ""
    return f"Repository {name} shows hands-on project work with {', '.join(observed)} based on committed files."


def _github_evidence_bullet(
    name: str,
    html_url: str,
    languages: list[str],
    frameworks: list[str],
    tools: list[str],
    repo: dict[str, Any],
) -> str:
    observed = _ordered_unique([*languages[:3], *frameworks[:4], *tools[:4]])
    description = _text(repo.get("description"))
    parts = [f"Public GitHub repository {name} ({_github_owner_repo(html_url)})"]
    if description:
        parts.append(f"is described as {description!r}")
    if observed:
        parts.append(f"and shows {', '.join(observed)} evidence")
    return " ".join(parts) + "."


def _github_owner_repo(url: str) -> str:
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else url


def _fetch_json(url: str) -> Any:
    import httpx

    response = httpx.get(
        url,
        timeout=30.0,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "BackendScout/0.1"},
    )
    response.raise_for_status()
    return response.json()


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
