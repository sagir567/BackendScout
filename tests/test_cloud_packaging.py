"""Offline packaging contracts; never read private config or contact a daemon/API."""

import ast
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = {
    f"config/{name}.example.yaml"
    for name in (
        "candidate_profile", "career_evidence", "cv_style", "target_companies",
        "scouting_preferences", "repo_sources",
    )
}
LANES = {"general", "browser", "telegram"}
COMPOSE = yaml.safe_load((ROOT / "compose.yaml").read_text())


def context_includes(path):
    """Evaluate this allowlist's anchored glob subset, including parent matches.

    Docker directory negations also admit descendants. Testing parents catches
    accidental broadening such as !config/ without a following config/** deny.
    This is not a general replacement for Docker's patternmatcher.
    """
    candidates = [path, *(str(p) for p in PurePosixPath(path).parents if str(p) != ".")]
    included = True
    for line in (ROOT / ".dockerignore").read_text().splitlines():
        rule = line.strip()
        if not rule or rule.startswith("#"):
            continue
        negated = rule.startswith("!")
        pattern = rule.lstrip("!").rstrip("/")
        regex = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
        if any(re.fullmatch(regex, candidate) for candidate in candidates):
            included = negated
    return included


def docker_instructions():
    text = (ROOT / "Dockerfile").read_text().replace("\\\n", " ")
    return [
        line.split(maxsplit=1) for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    ]


@pytest.mark.parametrize("path", sorted(EXAMPLES) + [
    "Dockerfile", ".dockerignore", "pyproject.toml", "uv.lock", "README.md",
    "src/backend_scout/cli.py", "src/backend_scout/__init__.py",
])
def test_allowlisted_build_inputs_are_available(path):
    assert context_includes(path)


@pytest.mark.parametrize("path", [
    ".env", ".env.production", ".git/config", ".git/objects/private",
    ".venv/bin/python", ".venv-old/lib/site-packages/token.json",
    "config/candidate_profile.yaml", "config/career_evidence.yaml",
    "config/gmail-client.json", "config/unreviewed.example.yaml",
    "config/nested/token.json", "config/private.py", "deploy/cloud.env",
    "deploy/cloud.env.example", "data/runtime/backendscout.sqlite3",
    "artifacts/candidate.pdf", "applications/company/CV.docx", "CV.pdf",
    "browser-profile/Default/Cookies", "secrets/gmail.json", "docs/private.md",
    "src/other_package/private.py", "src/private.json",
    "src/backend_scout/.env", "src/backend_scout/gmail.json",
    "src/backend_scout/__pycache__/config.cpython-312.pyc",
    "src/backend_scout/nested/private.py", "tests/fixture-with-private-data.yaml",
    "scripts/run_task_worker.sh", "launchd/com.backendscout.worker.plist.template",
    "previous-runtime/.venv/bin/python", "unreviewed-new-folder/credentials.json",
])
def test_private_and_unreviewed_paths_never_enter_context(path):
    assert not context_includes(path), path


def test_dockerfile_only_copies_allowlisted_code_and_examples():
    sources = set()
    for instruction, argument in docker_instructions():
        assert instruction != "ADD"
        if instruction == "COPY":
            tokens = shlex.split(argument)
            if tokens[0].startswith("--from="):
                assert tokens == ["--from=uv", "/uv", "/usr/local/bin/uv"]
            else:
                sources.update(tokens[:-1])
    assert sources == EXAMPLES | {
        "pyproject.toml", "uv.lock", "README.md", "src/backend_scout/",
    }
    assert all(context_includes(p) for p in sources if not p.endswith("/"))


def test_python_locked_install_linux_tools_and_nonroot_default():
    instructions = docker_instructions()
    text = (ROOT / "Dockerfile").read_text()
    assert ["FROM", "python:3.12-slim-bookworm"] in instructions
    assert "uv sync --locked --no-dev --no-editable --no-cache" in text
    assert "UV_PROJECT_ENVIRONMENT=/opt/venv" in text
    assert "playwright install --with-deps chromium" in text
    for package in ("libreoffice-writer", "fonts-crosextra-carlito", "fonts-noto-core",
                    "xvfb", "xauth", "util-linux"):
        assert package in text
    assert ["USER", "10001:10001"] in instructions
    assert ["WORKDIR", "/var/lib/backendscout"] in instructions
    assert "PLAYWRIGHT_BROWSERS_PATH=/opt/playwright" in text
    assert "HOME=/var/lib/backendscout/home" in text


def test_image_default_is_offline_check_not_cli_or_scheduler():
    command = json.loads(next(arg for op, arg in docker_instructions() if op == "CMD"))
    assert command[:2] == ["python", "-c"]
    imports = set()
    for node in ast.walk(ast.parse(command[2])):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module)
    assert imports == {
        "importlib.metadata", "os", "shutil", "sqlite3", "sys", "pathlib",
        "playwright.sync_api",
    }
    assert "':memory:'" in command[2]
    assert "launch(" not in command[2]
    services = COMPOSE["services"]
    assert {name for name, service in services.items() if not service.get("profiles")} == {"check"}
    check = services["check"]
    assert check["network_mode"] == "none"
    assert "command" not in check and "entrypoint" not in check
    assert not check.get("environment") and not check.get("env_file")
    assert [mount["type"] for mount in check["volumes"]] == ["volume"]


def test_all_lanes_share_persistent_state_and_mount_private_config_readonly():
    assert set(COMPOSE["services"]) == LANES | {"check"}
    assert set(COMPOSE["volumes"]) == {"state"}
    for name, service in COMPOSE["services"].items():
        assert service["user"] == "10001:10001"
        assert service["working_dir"] == "/var/lib/backendscout"
        assert service["init"] and service["read_only"]
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["restart"] == "no"
        state = service["volumes"][0]
        assert state == {"type": "volume", "source": "state", "target": "/var/lib/backendscout"}
        assert "env_file" not in service
        if name == "check":
            continue
        assert service["profiles"] == ["automation"]
        mounts = service["volumes"][1:]
        assert len(mounts) == 2
        assert {m["target"] for m in mounts} == {
            "/var/lib/backendscout/config", "/run/backendscout/secrets",
        }
        for mount in mounts:
            assert mount["type"] == "bind" and mount["read_only"]
            assert mount["bind"]["create_host_path"] is False
            assert mount["source"].startswith("${BACKENDSCOUT_PRIVATE_DIR:-/nonexistent/")
        env = service["environment"]
        assert env["GMAIL_CREDENTIAL_STORE"] == "file"
        assert env["GMAIL_CREDENTIAL_FILE"] == "/var/lib/backendscout/secrets/gmail.json"
        for key in ("GMAIL_OAUTH_CLIENT_SECRET_PATH", "WORKFLOW_DATABASE_PATH", "CV_ARCHIVE_ROOT",
                    "SUBMISSION_PROOF_ROOT", "BROWSER_PROFILE_ROOT"):
            assert env[key].startswith("/")
        for key in ("OPENAI_API_KEY", "OPENAI_MODEL_FAST", "OPENAI_MODEL_BALANCED",
                    "OPENAI_MODEL_HIGH_QUALITY", "NOTION_API_KEY", "TELEGRAM_BOT_TOKEN"):
            assert env[key] == "${" + key + ":-}"


def test_single_scheduler_browser_and_telegram_lane_contracts():
    services = COMPOSE["services"]
    owners = [name for name, s in services.items() if "--enable-schedules" in s.get("command", [])]
    assert owners == ["general"]
    assert services["general"]["command"] == [
        "backend-scout", "tasks", "worker", "--lane", "general", "--enable-schedules",
    ]
    browser = services["browser"]
    assert browser["command"][0] == "xvfb-run"
    assert "--server-args=-screen 0 1280x900x24 -nolisten tcp" in browser["command"]
    assert browser["command"][-5:] == ["backend-scout", "tasks", "worker", "--lane", "browser"]
    assert browser["shm_size"] == "1gb"
    assert services["telegram"]["command"][:5] == [
        "backend-scout", "telegram", "listen", "--tracker", "production",
    ]
    assert len({services[name]["container_name"] for name in LANES}) == 3
    for name in LANES:
        service = services[name]
        assert service["deploy"]["replicas"] == 1
        assert service["environment"]["BACKENDSCOUT_LANE"] == name
        entry = service["entrypoint"]
        assert entry[:2] == ["/bin/sh", "-ec"] and entry[-1] == "--"
        assert 'flock --no-fork --nonblock --conflict-exit-code 75' in entry[2]
        assert 'data/runtime/$${BACKENDSCOUT_LANE}.lock' in entry[2]
        assert '"$$@"' in entry[2]


def test_no_browser_ui_ports_or_macos_runtime_in_compose():
    for service in COMPOSE["services"].values():
        assert not service.get("ports") and not service.get("expose")
        assert service.get("network_mode") != "host"
        assert not service.get("privileged") and not service.get("devices")
        command = " ".join(service.get("command", []))
        assert not any(word in command for word in ("launchd", "scripts/", "system runtime", "uv run"))
        if service.get("profiles"):
            assert service["environment"]["VERIFICATION_HANDOFF_ENABLED"] == "false"


@pytest.mark.parametrize(("overrides", "message"), [
    ({}, "Automation disabled"),
    ({"BACKENDSCOUT_ENABLE_AUTOMATION": "true"}, "Automation disabled"),
    ({"BACKENDSCOUT_ENABLE_AUTOMATION": "yes-i-understand"}, "sole-scheduler"),
    ({"BACKENDSCOUT_ENABLE_AUTOMATION": "yes-i-understand",
      "BACKENDSCOUT_SCHEDULER_OWNERSHIP": "cloud-is-sole-scheduler",
      "BACKENDSCOUT_PRIVATE_DIR": "./private"}, "absolute BACKENDSCOUT_PRIVATE_DIR"),
    ({"BACKENDSCOUT_ENABLE_AUTOMATION": "yes-i-understand",
      "BACKENDSCOUT_SCHEDULER_OWNERSHIP": "cloud-is-sole-scheduler",
      "BACKENDSCOUT_PRIVATE_DIR": "/synthetic/private", "OPENAI_API_KEY": ""},
     "Set OPENAI_API_KEY externally"),
])
@pytest.mark.parametrize("lane", sorted(LANES))
def test_guard_refuses_unacknowledged_or_incomplete_automation(tmp_path, lane, overrides, message):
    script = COMPOSE["services"][lane]["entrypoint"][2].replace("$$", "$")
    sentinel = tmp_path / "workflow-must-not-run"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "OPENAI_API_KEY": "synthetic-secret-must-not-appear",
        "NOTION_API_KEY": "synthetic-secret-must-not-appear",
        "TELEGRAM_BOT_TOKEN": "synthetic-secret-must-not-appear",
        **overrides,
    }
    result = subprocess.run(
        ["/bin/sh", "-ec", script, "--", "touch", str(sentinel)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode != 0
    assert message in result.stderr
    assert "synthetic-secret-must-not-appear" not in result.stdout + result.stderr
    assert not sentinel.exists()
    assert not (tmp_path / "data").exists()


def test_example_environment_is_empty_and_disabled():
    values = dict(
        line.split("=", 1) for line in (ROOT / "deploy/cloud.env.example").read_text().splitlines()
        if line and not line.startswith("#")
    )
    assert values["BACKENDSCOUT_ENABLE_AUTOMATION"] == ""
    assert values["BACKENDSCOUT_SCHEDULER_OWNERSHIP"] == ""
    for key in ("OPENAI_API_KEY", "OPENAI_MODEL_FAST", "OPENAI_MODEL_BALANCED",
                "OPENAI_MODEL_HIGH_QUALITY", "NOTION_API_KEY", "TELEGRAM_BOT_TOKEN"):
        assert values[key] == ""
    assert values["GMAIL_CREDENTIAL_STORE"] == "file"
    assert values["GMAIL_CREDENTIAL_FILE"].startswith("/")
    assert values["BACKENDSCOUT_PRIVATE_DIR"].startswith("/")
    assert not values.get("COMPOSE_PROFILES")


def test_runbook_shell_blocks_parse_without_running_commands():
    blocks = re.findall(r"```sh\n(.*?)\n```", (ROOT / "CLOUD_DEPLOYMENT.md").read_text(), re.DOTALL)
    assert blocks
    for block in blocks:
        result = subprocess.run(
            ["/bin/sh", "-n"], input=block, capture_output=True, text=True,
            timeout=10, check=False,
        )
        assert result.returncode == 0, result.stderr


def test_real_compose_config_with_synthetic_environment_no_daemon(tmp_path):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path),
        "DOCKER_HOST": "unix:///synthetic-no-daemon.sock",
        "BACKENDSCOUT_PRIVATE_DIR": "/synthetic/private",
        "OPENAI_API_KEY": "synthetic-not-a-key", "OPENAI_MODEL_FAST": "synthetic-fast",
        "OPENAI_MODEL_BALANCED": "synthetic-balanced", "OPENAI_MODEL_HIGH_QUALITY": "synthetic-hq",
        "NOTION_API_KEY": "synthetic-notion", "TELEGRAM_BOT_TOKEN": "synthetic-telegram",
        "TELEGRAM_DEFAULT_CHAT_ID": "123", "TELEGRAM_ALLOWED_USER_IDS": "123",
        "NOTION_PRODUCTION_APPLICATIONS_DATA_SOURCE_ID": "synthetic-production",
    }
    compose = None
    for candidate in (["docker", "compose"], ["docker-compose"]):
        if not shutil.which(candidate[0]):
            continue
        probe = subprocess.run(
            [*candidate, "version"], env=env, capture_output=True, text=True,
            timeout=20, check=False,
        )
        if probe.returncode == 0:
            compose = candidate
            break
    if compose is None:
        pytest.skip("Docker Compose is not installed; static and shell guard contracts still run")
    base = [*compose, "--project-directory", str(ROOT), "--env-file", "/dev/null"]
    default = subprocess.run(
        [*base, "config", "--services"], env=env, capture_output=True, text=True,
        timeout=20, check=False,
    )
    assert default.returncode == 0, default.stderr
    assert default.stdout.split() == ["check"]
    result = subprocess.run(
        [*base, "--profile", "automation", "config", "--format", "json"],
        env=env, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    assert set(rendered["services"]) == LANES | {"check"}
    for name in LANES:
        service = rendered["services"][name]
        assert service["environment"]["OPENAI_MODEL_FAST"] == "synthetic-fast"
        assert service["environment"]["GMAIL_CREDENTIAL_STORE"] == "file"
        assert service["volumes"][1]["source"] == "/synthetic/private/config"
        assert service["volumes"][2]["source"] == "/synthetic/private/secrets"
        assert not service.get("ports")
    assert "environment" not in rendered["services"]["check"]
