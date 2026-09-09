from pathlib import Path

from backend_scout.launchd import (
    LaunchdService,
    agent_path,
    install_launchd_service,
    launchd_service_installed,
    render_launchd_template,
    selected_services,
    uninstall_launchd_service,
)


def test_selected_services_all_uses_runtime_order() -> None:
    assert selected_services(LaunchdService.ALL) == [
        LaunchdService.TELEGRAM,
        LaunchdService.WORKER,
        LaunchdService.MAILBOX,
        LaunchdService.DAILY,
    ]


def test_render_launchd_template_replaces_project_root() -> None:
    content = render_launchd_template(Path.cwd(), LaunchdService.DAILY)

    assert "TODO_ABSOLUTE_PROJECT_PATH" not in content
    assert str(Path.cwd().resolve()) in content


def test_install_and_uninstall_launchd_service(tmp_path: Path) -> None:
    path = install_launchd_service(Path.cwd(), tmp_path, LaunchdService.DAILY)

    assert path == agent_path(tmp_path, LaunchdService.DAILY)
    assert launchd_service_installed(tmp_path, LaunchdService.DAILY)

    removed_path = uninstall_launchd_service(tmp_path, LaunchdService.DAILY)

    assert removed_path == path
    assert not path.exists()


def test_install_launchd_service_replace_bootouts_before_bootstrap(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    class FakeRunner:
        @staticmethod
        def run(command: list[str], check: bool) -> None:
            calls.append(command)

    install_launchd_service(Path.cwd(), tmp_path, LaunchdService.DAILY, load=True, replace=True, runner=FakeRunner)

    assert calls[0][:2] == ["launchctl", "bootout"]
    assert calls[1][:2] == ["launchctl", "bootstrap"]
