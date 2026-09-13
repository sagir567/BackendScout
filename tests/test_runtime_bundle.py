from pathlib import Path

from backend_scout.runtime_bundle import deploy_runtime_files, runtime_manifest


def _source_tree(root: Path) -> None:
    for directory in ("config", "launchd", "scripts", "src"):
        (root / directory).mkdir(parents=True)
        (root / directory / "placeholder.txt").write_text(directory, encoding="utf-8")
    (root / "data" / "telegram").mkdir(parents=True)
    (root / "data" / "telegram" / "last_update_id.txt").write_text("41", encoding="utf-8")
    (root / ".env").write_text(
        "SECRET=kept-private\nCV_ARCHIVE_ROOT=/old/archive\n",
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (root / "uv.lock").write_text("lock", encoding="utf-8")


def test_deploy_runtime_rewrites_private_paths_and_preserves_state(tmp_path: Path) -> None:
    source = tmp_path / "source"
    runtime = tmp_path / "runtime"
    source.mkdir()
    _source_tree(source)

    deploy_runtime_files(source, runtime)

    env = (runtime / ".env").read_text(encoding="utf-8")
    assert "SECRET=kept-private" in env
    assert f'CV_ARCHIVE_ROOT="{runtime}/private/cv_archive"' in env
    assert f'BROWSER_PROFILE_ROOT="{runtime}/private/browser_profile"' in env
    assert (runtime / "data" / "telegram" / "last_update_id.txt").read_text() == "41"

    (runtime / "data" / "telegram" / "last_update_id.txt").write_text("99", encoding="utf-8")
    deploy_runtime_files(source, runtime)

    assert (runtime / "data" / "telegram" / "last_update_id.txt").read_text() == "99"
    assert runtime_manifest(runtime) is not None
