"""Deploy a private macOS runtime outside TCC-protected Documents folders."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_RUNTIME_ROOT = Path.home() / "Library" / "Application Support" / "BackendScout"
RUNTIME_MANIFEST_NAME = "runtime-manifest.json"
RUNTIME_DIRECTORIES = ("config", "launchd", "scripts", "src")
RUNTIME_FILES = (".env", "pyproject.toml", "uv.lock")
RUNTIME_ENV_OVERRIDES = {
    "CV_ARCHIVE_ROOT": "private/cv_archive",
    "BROWSER_PROFILE_ROOT": "private/browser_profile",
    "SUBMISSION_PROOF_ROOT": "private/submission_proofs",
    "WORKFLOW_DATABASE_PATH": "private/backendscout.sqlite3",
}


def deploy_runtime_files(source_root: Path, runtime_root: Path) -> Path:
    source = source_root.resolve()
    runtime = runtime_root.expanduser().resolve()
    _validate_source(source)
    runtime.mkdir(parents=True, exist_ok=True)
    runtime.chmod(0o700)

    for relative_path in RUNTIME_FILES:
        shutil.copy2(source / relative_path, runtime / relative_path)
    for relative_path in RUNTIME_DIRECTORIES:
        destination = runtime / relative_path
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source / relative_path, destination)

    runtime_data = runtime / "data"
    if not runtime_data.exists():
        source_data = source / "data"
        if source_data.exists():
            shutil.copytree(
                source_data,
                runtime_data,
                ignore=shutil.ignore_patterns("logs", "*.lock", "*.tmp"),
            )
        else:
            runtime_data.mkdir(parents=True)
    (runtime / "private").mkdir(exist_ok=True)
    (runtime / "private").chmod(0o700)
    runtime_data.chmod(0o700)
    (runtime / "config").chmod(0o700)
    _rewrite_runtime_env(runtime / ".env", runtime)
    (runtime / ".env").chmod(0o600)
    for private_config in (runtime / "config").glob("*.yaml"):
        if not private_config.name.endswith(".example.yaml"):
            private_config.chmod(0o600)

    manifest_path = runtime / RUNTIME_MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(
            {
                "source_root": str(source),
                "runtime_root": str(runtime),
                "deployed_at": datetime.now(UTC).isoformat(),
                "private_state_preserved": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)
    return manifest_path


def runtime_manifest(runtime_root: Path) -> dict[str, object] | None:
    path = runtime_root.expanduser() / RUNTIME_MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def runtime_private_path(runtime_root: Path, relative_path: str) -> Path:
    return runtime_root.expanduser().resolve() / relative_path


def _rewrite_runtime_env(path: Path, runtime_root: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    overrides = {
        key: str(runtime_private_path(runtime_root, relative_path))
        for key, relative_path in RUNTIME_ENV_OVERRIDES.items()
    }
    written: set[str] = set()
    updated: list[str] = []
    for line in lines:
        key = (
            line.split("=", 1)[0].strip()
            if "=" in line and not line.lstrip().startswith("#")
            else None
        )
        if key in overrides:
            updated.append(f"{key}={json.dumps(overrides[key])}")
            written.add(key)
        else:
            updated.append(line)
    for key, value in overrides.items():
        if key not in written:
            updated.append(f"{key}={json.dumps(value)}")
    path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


def _validate_source(source_root: Path) -> None:
    missing = [
        str(source_root / relative_path)
        for relative_path in (*RUNTIME_FILES, *RUNTIME_DIRECTORIES)
        if not (source_root / relative_path).exists()
    ]
    if missing:
        raise ValueError("Runtime source is incomplete: " + ", ".join(missing))
