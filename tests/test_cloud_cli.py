from types import SimpleNamespace

from typer.testing import CliRunner

from backend_scout.cli import app


def test_headless_gmail_connect_forwards_fixed_port_and_uses_file_label(monkeypatch, tmp_path):
    client = tmp_path / "client.json"
    client.write_text("{}")
    monkeypatch.setattr(
        "backend_scout.cli.Settings",
        lambda: SimpleNamespace(
            gmail_oauth_client_secret_path=client,
            gmail_credential_store="file",
        ),
    )
    calls = []
    monkeypatch.setattr(
        "backend_scout.cli.connect_gmail", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    result = CliRunner().invoke(app, ["gmail", "connect", "--port", "8765", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert calls == [((client,), {"port": 8765, "open_browser": False})]
    assert "private credential file" in result.output
    assert "Keychain" not in result.output


def test_existing_gmail_connect_keeps_keyring_defaults(monkeypatch, tmp_path):
    client = tmp_path / "client.json"
    client.write_text("{}")
    monkeypatch.setattr(
        "backend_scout.cli.Settings",
        lambda: SimpleNamespace(
            gmail_oauth_client_secret_path=client,
            gmail_credential_store="keyring",
        ),
    )
    calls = []
    monkeypatch.setattr(
        "backend_scout.cli.connect_gmail", lambda *args, **kwargs: calls.append(kwargs)
    )
    result = CliRunner().invoke(app, ["gmail", "connect"])
    assert result.exit_code == 0, result.output
    assert calls == [{"port": 0, "open_browser": True}]
    assert "Keychain" in result.output
