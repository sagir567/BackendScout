"""Render and manage BackendScout macOS launchd agent plist files."""

import subprocess
from enum import Enum
from pathlib import Path


class LaunchdService(str, Enum):
    DAILY = "daily"
    TELEGRAM = "telegram"
    MAILBOX = "mailbox"
    WORKER = "worker"
    ALL = "all"


SERVICE_LABELS = {
    LaunchdService.DAILY: "com.backendscout.daily",
    LaunchdService.TELEGRAM: "com.backendscout.telegram",
    LaunchdService.MAILBOX: "com.backendscout.mailbox",
    LaunchdService.WORKER: "com.backendscout.worker",
}


def selected_services(service: LaunchdService) -> list[LaunchdService]:
    if service == LaunchdService.ALL:
        return [LaunchdService.TELEGRAM, LaunchdService.WORKER, LaunchdService.MAILBOX, LaunchdService.DAILY]
    return [service]


def template_path(project_root: Path, service: LaunchdService) -> Path:
    label = SERVICE_LABELS[service]
    return project_root / "launchd" / f"{label}.plist.template"


def agent_path(agent_dir: Path, service: LaunchdService) -> Path:
    return agent_dir / f"{SERVICE_LABELS[service]}.plist"


def render_launchd_template(project_root: Path, service: LaunchdService) -> str:
    template = template_path(project_root, service).read_text(encoding="utf-8")
    return template.replace("TODO_ABSOLUTE_PROJECT_PATH", str(project_root.resolve()))


def install_launchd_service(
    project_root: Path,
    agent_dir: Path,
    service: LaunchdService,
    *,
    load: bool = False,
    replace: bool = False,
    runner: type[subprocess] = subprocess,
) -> Path:
    agent_dir.mkdir(parents=True, exist_ok=True)
    path = agent_path(agent_dir, service)
    path.write_text(render_launchd_template(project_root, service), encoding="utf-8")
    if load:
        if replace:
            runner.run(["launchctl", "bootout", f"gui/{_user_id()}", str(path)], check=False)
        runner.run(["launchctl", "bootstrap", f"gui/{_user_id()}", str(path)], check=True)
    return path


def uninstall_launchd_service(
    agent_dir: Path,
    service: LaunchdService,
    *,
    unload: bool = False,
    runner: type[subprocess] = subprocess,
) -> Path:
    path = agent_path(agent_dir, service)
    if unload:
        runner.run(["launchctl", "bootout", f"gui/{_user_id()}", str(path)], check=False)
    path.unlink(missing_ok=True)
    return path


def launchd_service_installed(agent_dir: Path, service: LaunchdService) -> bool:
    return agent_path(agent_dir, service).exists()


def _user_id() -> int:
    import os

    return os.getuid()
