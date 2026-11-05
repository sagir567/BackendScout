import builtins
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend_scout.secrets import (
    MAX_TOKEN_BYTES,
    CredentialStoreError,
    FileCredentialStore,
    KeyringCredentialStore,
    get_credential_store,
)

TOKEN = '{"token":"synthetic-access-token","refresh_token":"synthetic-refresh-token"}'
UPDATED_TOKEN = '{"token":"synthetic-refreshed-token","refresh_token":"synthetic-refresh-token"}'


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GMAIL_CREDENTIAL_STORE", raising=False)
    monkeypatch.delenv("GMAIL_CREDENTIAL_FILE", raising=False)


@pytest.fixture
def token_path(tmp_path: Path) -> Path:
    directory = tmp_path / "credentials"
    directory.mkdir(mode=0o700)
    return directory / "gmail.json"


@pytest.mark.parametrize("provider", [None, "keyring"])
def test_keyring_remains_default_and_ignores_file_path(monkeypatch, provider) -> None:
    calls = []
    fake_keyring = SimpleNamespace(
        get_password=lambda *args: calls.append(("get", args)) or TOKEN,
        set_password=lambda *args: calls.append(("set", args)),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    monkeypatch.setenv("GMAIL_CREDENTIAL_FILE", "ignored-relative-path")
    if provider is not None:
        monkeypatch.setenv("GMAIL_CREDENTIAL_STORE", provider)

    store = get_credential_store("service", "account")

    assert isinstance(store, KeyringCredentialStore)
    assert store.load() == TOKEN
    store.save(UPDATED_TOKEN)
    assert calls == [
        ("get", ("service", "account")),
        ("set", ("service", "account", UPDATED_TOKEN)),
    ]


@pytest.mark.parametrize("provider", ["", "unknown", "FILE", " keyring"])
def test_unknown_provider_fails_closed(monkeypatch, provider: str) -> None:
    monkeypatch.setenv("GMAIL_CREDENTIAL_STORE", provider)
    with pytest.raises(CredentialStoreError, match="must be keyring or file"):
        get_credential_store("service", "account")


@pytest.mark.parametrize("path", [None, "", "gmail.json", "~/gmail.json", "/", "/a/../b"])
def test_file_requires_explicit_absolute_path(monkeypatch, path) -> None:
    monkeypatch.setenv("GMAIL_CREDENTIAL_STORE", "file")
    if path is not None:
        monkeypatch.setenv("GMAIL_CREDENTIAL_FILE", path)
    with pytest.raises(CredentialStoreError):
        get_credential_store("service", "account")


def test_provider_selection_reads_dotenv(monkeypatch, token_path: Path, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        f"GMAIL_CREDENTIAL_STORE=file\nGMAIL_CREDENTIAL_FILE={token_path}\n"
    )

    store = get_credential_store("service", "account")

    assert isinstance(store, FileCredentialStore)
    assert store.path == token_path
    store.save(TOKEN)
    assert store.load() == TOKEN
    monkeypatch.setenv("GMAIL_CREDENTIAL_STORE", "keyring")
    assert isinstance(get_credential_store("service", "account"), KeyringCredentialStore)


def test_file_backend_never_imports_or_calls_keyring(monkeypatch, token_path: Path) -> None:
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "keyring" or name.startswith("keyring."):
            pytest.fail("The file provider must not import keyring")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setenv("GMAIL_CREDENTIAL_STORE", "file")
    monkeypatch.setenv("GMAIL_CREDENTIAL_FILE", str(token_path))
    store = get_credential_store("service", "account")

    assert isinstance(store, FileCredentialStore)
    assert store.load() is None
    store.save(TOKEN)
    assert store.load() == TOKEN
    store.save(UPDATED_TOKEN)
    assert store.load() == UPDATED_TOKEN


def test_missing_file_or_directory_is_not_connected_and_does_not_create(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path / "absent" / "nested" / "gmail.json")
    assert store.load() is None
    assert list(tmp_path.iterdir()) == []


def test_save_creates_private_directories_and_file_even_with_permissive_umask(
    tmp_path: Path,
) -> None:
    path = tmp_path / "new" / "nested" / "gmail.json"
    original_umask = os.umask(0)
    try:
        FileCredentialStore(path).save(TOKEN)
    finally:
        os.umask(original_umask)

    assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.stat().st_uid == os.geteuid()
    assert FileCredentialStore(path).load() == TOKEN


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o660, 0o666, 0o400])
@pytest.mark.parametrize("operation", ["load", "save"])
def test_rejects_unsafe_file_permissions(token_path: Path, mode: int, operation: str) -> None:
    token_path.write_text(TOKEN)
    token_path.chmod(mode)
    store = FileCredentialStore(token_path)
    with pytest.raises(CredentialStoreError, match="0600 file"):
        store.load() if operation == "load" else store.save(UPDATED_TOKEN)
    assert token_path.read_text() == TOKEN
    assert stat.S_IMODE(token_path.stat().st_mode) == mode


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o770, 0o777, 0o1700])
@pytest.mark.parametrize("operation", ["load", "save"])
def test_rejects_unsafe_directory_permissions(token_path: Path, mode: int, operation: str) -> None:
    token_path.parent.chmod(mode)
    store = FileCredentialStore(token_path)
    with pytest.raises(CredentialStoreError, match="directory must be"):
        store.load() if operation == "load" else store.save(TOKEN)
    assert not token_path.exists()
    assert stat.S_IMODE(token_path.parent.stat().st_mode) == mode


def test_rejects_writable_ancestor_even_with_private_parent(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o700)
    unsafe.chmod(0o777)
    private = unsafe / "private"
    private.mkdir(mode=0o700)
    store = FileCredentialStore(private / "gmail.json")
    with pytest.raises(CredentialStoreError, match="unsafe ancestor"):
        store.save(TOKEN)
    assert list(private.iterdir()) == []


@pytest.mark.parametrize("kind", ["file", "directory"])
@pytest.mark.parametrize("operation", ["load", "save"])
def test_rejects_wrong_owner(monkeypatch, token_path: Path, kind: str, operation: str) -> None:
    store = FileCredentialStore(token_path)
    store.save(TOKEN)
    original_fstat = os.fstat
    target_inode = (token_path if kind == "file" else token_path.parent).stat().st_ino

    def wrong_owner(descriptor):
        info = original_fstat(descriptor)
        if info.st_ino == target_inode:
            return SimpleNamespace(
                st_uid=os.geteuid() + 1, st_mode=info.st_mode, st_nlink=info.st_nlink
            )
        return info

    # Reads validate the opened descriptor, writes validate the destination's lstat.
    original_stat = os.stat

    def wrong_stat(*args, **kwargs):
        info = original_stat(*args, **kwargs)
        if kind == "file" and kwargs.get("follow_symlinks") is False:
            return SimpleNamespace(
                st_uid=os.geteuid() + 1, st_mode=info.st_mode, st_nlink=info.st_nlink
            )
        return info

    monkeypatch.setattr(os, "fstat", wrong_owner)
    monkeypatch.setattr(os, "stat", wrong_stat)
    with pytest.raises(CredentialStoreError):
        store.load() if operation == "load" else store.save(UPDATED_TOKEN)
    assert token_path.read_text() == TOKEN


@pytest.mark.parametrize("operation", ["load", "save"])
@pytest.mark.parametrize("dangling", [False, True])
def test_rejects_file_symlinks(token_path: Path, operation: str, dangling: bool) -> None:
    target = token_path.parent / "other.json"
    if not dangling:
        target.write_text(TOKEN)
        target.chmod(0o600)
    token_path.symlink_to(target)
    store = FileCredentialStore(token_path)
    with pytest.raises(CredentialStoreError):
        store.load() if operation == "load" else store.save(UPDATED_TOKEN)
    assert token_path.is_symlink()
    assert target.exists() is not dangling
    if not dangling:
        assert target.read_text() == TOKEN


@pytest.mark.parametrize("operation", ["load", "save"])
@pytest.mark.parametrize("nested", [False, True])
def test_rejects_directory_and_ancestor_symlinks(
    tmp_path: Path, operation: str, nested: bool
) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "linked"
    link.symlink_to(real, target_is_directory=True)
    path = link / "nested" / "gmail.json" if nested else link / "gmail.json"
    store = FileCredentialStore(path)
    with pytest.raises(CredentialStoreError):
        store.load() if operation == "load" else store.save(TOKEN)
    assert list(real.iterdir()) == []


@pytest.mark.parametrize("kind", ["directory", "fifo", "hardlink"])
@pytest.mark.parametrize("operation", ["load", "save"])
def test_rejects_nonregular_or_multilink_token(token_path: Path, kind: str, operation: str) -> None:
    if kind == "directory":
        token_path.mkdir(mode=0o700)
    elif kind == "fifo":
        os.mkfifo(token_path, mode=0o600)
    else:
        original = token_path.parent / "original.json"
        original.write_text(TOKEN)
        original.chmod(0o600)
        token_path.hardlink_to(original)
    store = FileCredentialStore(token_path)
    with pytest.raises(CredentialStoreError, match="0600 file"):
        store.load() if operation == "load" else store.save(UPDATED_TOKEN)


@pytest.mark.parametrize("data", [b"", b"not JSON", b"[]", b"null", b'"token"', b"\xff"])
def test_load_rejects_malformed_or_nonobject_json(token_path: Path, data: bytes) -> None:
    token_path.write_bytes(data)
    token_path.chmod(0o600)
    with pytest.raises(CredentialStoreError):
        FileCredentialStore(token_path).load()


@pytest.mark.parametrize("token", ["", "not JSON", "[]", "null", '"token"', "\ud800"])
def test_invalid_save_does_not_change_existing_token(token_path: Path, token: str) -> None:
    store = FileCredentialStore(token_path)
    store.save(TOKEN)
    with pytest.raises(CredentialStoreError):
        store.save(token)
    assert store.load() == TOKEN
    assert list(token_path.parent.iterdir()) == [token_path]


def test_json_limit_counts_bytes_and_accepts_exact_boundary(token_path: Path) -> None:
    store = FileCredentialStore(token_path)
    boundary = '{"token":"' + "a" * (MAX_TOKEN_BYTES - len('{"token":""}')) + '"}'
    assert len(boundary.encode()) == MAX_TOKEN_BYTES
    store.save(boundary)
    assert store.load() == boundary
    multibyte = json.dumps({"token": "\u00e9" * (MAX_TOKEN_BYTES // 2)}, ensure_ascii=False)
    assert len(multibyte) < MAX_TOKEN_BYTES < len(multibyte.encode())
    for oversized in (boundary + " ", multibyte):
        with pytest.raises(CredentialStoreError):
            store.save(oversized)
        assert store.load() == boundary


def test_oversized_read_is_rejected_without_reading_payload(monkeypatch, token_path: Path) -> None:
    token_path.write_bytes(b"x" * (MAX_TOKEN_BYTES + 1))
    token_path.chmod(0o600)
    original_fdopen = os.fdopen

    class NoRead:
        def __init__(self, descriptor, **kwargs):
            self.stream = original_fdopen(descriptor, "rb", **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, _size):
            pytest.fail("An oversized file must be rejected before reading")

    monkeypatch.setattr(os, "fdopen", lambda descriptor, _mode, **kw: NoRead(descriptor, **kw))
    with pytest.raises(CredentialStoreError, match="size limit"):
        FileCredentialStore(token_path).load()


def test_read_is_bounded_even_if_file_grows(monkeypatch, token_path: Path) -> None:
    token_path.write_bytes(b"x" * (MAX_TOKEN_BYTES + 1))
    token_path.chmod(0o600)
    original_fstat = os.fstat
    original_fdopen = os.fdopen
    sizes = []

    def small_stat(descriptor):
        info = original_fstat(descriptor)
        if stat.S_ISREG(info.st_mode):
            return SimpleNamespace(
                st_uid=info.st_uid, st_mode=info.st_mode, st_nlink=info.st_nlink, st_size=0
            )
        return info

    class BoundedRead:
        def __init__(self, descriptor, **kwargs):
            self.stream = original_fdopen(descriptor, "rb", **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            sizes.append(size)
            return self.stream.read(size)

    monkeypatch.setattr(os, "fstat", small_stat)
    monkeypatch.setattr(os, "fdopen", lambda descriptor, _mode, **kw: BoundedRead(descriptor, **kw))
    with pytest.raises(CredentialStoreError, match="size limit"):
        FileCredentialStore(token_path).load()
    assert sizes == [MAX_TOKEN_BYTES + 1]


def test_save_atomically_replaces_0600_file_and_syncs(monkeypatch, token_path: Path) -> None:
    store = FileCredentialStore(token_path)
    store.save(TOKEN)
    old_inode = token_path.stat().st_ino
    original_replace = os.replace
    original_fsync = os.fsync
    synced = []
    replacements = []

    def checked_replace(source, destination, *, src_dir_fd, dst_dir_fd):
        assert token_path.read_text() == TOKEN
        assert src_dir_fd == dst_dir_fd
        info = os.stat(source, dir_fd=src_dir_fd, follow_symlinks=False)
        assert stat.S_IMODE(info.st_mode) == 0o600
        assert info.st_uid == os.geteuid()
        assert synced == ["file"]
        replacements.append(destination)
        original_replace(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    def checked_fsync(descriptor):
        synced.append("directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "replace", checked_replace)
    monkeypatch.setattr(os, "fsync", checked_fsync)
    store.save(UPDATED_TOKEN)

    assert replacements == [token_path.name]
    assert synced == ["file", "directory"]
    assert token_path.stat().st_ino != old_inode
    assert store.load() == UPDATED_TOKEN
    assert list(token_path.parent.iterdir()) == [token_path]


@pytest.mark.parametrize("failure", ["replace", "fsync"])
def test_failed_save_preserves_old_token_and_cleans_temporary(
    monkeypatch, token_path: Path, failure: str
) -> None:
    store = FileCredentialStore(token_path)
    store.save(TOKEN)

    def fail(*_args, **_kwargs):
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(os, failure, fail)
    with pytest.raises(CredentialStoreError):
        store.save(UPDATED_TOKEN)
    assert store.load() == TOKEN
    assert list(token_path.parent.iterdir()) == [token_path]


def test_rechecks_destination_before_replace(monkeypatch, token_path: Path) -> None:
    store = FileCredentialStore(token_path)
    store.save(TOKEN)
    original_fsync = os.fsync

    def unsafe_destination(descriptor):
        original_fsync(descriptor)
        token_path.chmod(0o644)

    monkeypatch.setattr(os, "fsync", unsafe_destination)
    with pytest.raises(CredentialStoreError, match="0600 file"):
        store.save(UPDATED_TOKEN)
    assert token_path.read_text() == TOKEN
    assert list(token_path.parent.iterdir()) == [token_path]


def test_errors_and_logs_do_not_disclose_token(
    monkeypatch, token_path: Path, caplog, capsys
) -> None:
    store = FileCredentialStore(token_path)
    store.save(TOKEN)
    with pytest.raises(CredentialStoreError) as malformed:
        store.save(TOKEN + " not JSON")

    def fail(*_args, **_kwargs):
        raise OSError(UPDATED_TOKEN)

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(CredentialStoreError) as io_error:
        store.save(UPDATED_TOKEN)

    output = capsys.readouterr()
    diagnostics = caplog.text + output.out + output.err + str(malformed.value) + str(io_error.value)
    assert "synthetic-access-token" not in diagnostics
    assert "synthetic-refreshed-token" not in diagnostics
    assert "synthetic-refresh-token" not in diagnostics
    assert io_error.value.__suppress_context__
