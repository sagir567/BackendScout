"""Opt-in Gmail token storage; the default provider remains the OS keyring."""

import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

MAX_TOKEN_BYTES = 64 * 1024


class CredentialStoreError(ValueError):
    """A credential store is misconfigured, unsafe or unreadable."""


class CredentialStore(Protocol):
    def load(self) -> str | None: ...

    def save(self, token: str) -> None: ...


@dataclass(frozen=True)
class KeyringCredentialStore:
    service: str
    account: str

    def load(self) -> str | None:
        import keyring

        return keyring.get_password(self.service, self.account)

    def save(self, token: str) -> None:
        import keyring

        keyring.set_password(self.service, self.account, token)


@dataclass(frozen=True)
class FileCredentialStore:
    """Store bounded JSON in a private POSIX directory, never following symlinks.

    Existing parent/token permissions must be 0700/0600 and owned by this UID.
    Missing directories are created only on save. Ancestors may be shared only
    when they are root-owned sticky directories (for example Linux /tmp).
    """

    path: Path

    def __post_init__(self) -> None:
        if not self.path.is_absolute() or ".." in self.path.parts or not self.path.name:
            raise CredentialStoreError(
                "GMAIL_CREDENTIAL_FILE must be an absolute private file path."
            )

    def load(self) -> str | None:
        with self._directory(create=False) as directory:
            if directory is None:
                return None
            try:
                descriptor = os.open(
                    self.path.name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                    dir_fd=directory,
                )
            except FileNotFoundError:
                return None
            try:
                info = os.fstat(descriptor)
                self._check_file(info)
                if info.st_size > MAX_TOKEN_BYTES:
                    raise CredentialStoreError("Gmail credential JSON exceeds the size limit.")
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    data = stream.read(MAX_TOKEN_BYTES + 1)
            finally:
                os.close(descriptor)
            if len(data) > MAX_TOKEN_BYTES:
                raise CredentialStoreError("Gmail credential JSON exceeds the size limit.")
            try:
                token = data.decode("utf-8")
            except UnicodeError:
                raise CredentialStoreError(
                    "Gmail credentials must be a UTF-8 JSON object."
                ) from None
            _token_bytes(token)
            return token

    def save(self, token: str) -> None:
        data = _token_bytes(token)
        with self._directory(create=True) as directory:
            assert directory is not None
            self._check_destination(directory)
            temporary = f".gmail-token-{os.urandom(16).hex()}.tmp"
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory,
            )
            try:
                try:
                    with os.fdopen(descriptor, "wb", closefd=False) as stream:
                        os.fchmod(stream.fileno(), 0o600)
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                finally:
                    os.close(descriptor)
                self._check_destination(directory)
                os.replace(temporary, self.path.name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass

    @contextmanager
    def _directory(self, *, create: bool) -> Iterator[int | None]:
        # Walk relative to validated descriptors so ancestor swaps cannot redirect I/O.
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        directory = os.open("/", flags)
        try:
            self._check_directory(directory, private=False)
            parts = self.path.parts[1:-1]
            for index, part in enumerate(parts):
                try:
                    child = os.open(part, flags, dir_fd=directory)
                except FileNotFoundError:
                    if not create:
                        yield None
                        return
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=directory)
                    except FileExistsError:
                        pass
                    child = os.open(part, flags, dir_fd=directory)
                os.close(directory)
                directory = child
                self._check_directory(directory, private=index == len(parts) - 1)
            if not parts:
                self._check_directory(directory, private=True)
            yield directory
        except OSError:
            raise CredentialStoreError("Cannot access Gmail credential file safely.") from None
        finally:
            os.close(directory)

    @staticmethod
    def _check_directory(descriptor: int, *, private: bool) -> None:
        info = os.fstat(descriptor)
        mode = stat.S_IMODE(info.st_mode)
        if private:
            if info.st_uid != os.geteuid() or mode != 0o700:
                raise CredentialStoreError(
                    "Gmail credential directory must be owner-owned and 0700."
                )
        elif info.st_uid not in {0, os.geteuid()} or (
            mode & 0o022 and not (info.st_uid == 0 and mode & stat.S_ISVTX)
        ):
            raise CredentialStoreError("Gmail credential path has an unsafe ancestor directory.")

    @staticmethod
    def _check_file(info: os.stat_result) -> None:
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise CredentialStoreError(
                "Gmail credentials require an owner-owned, single-link 0600 file."
            )

    def _check_destination(self, directory: int) -> None:
        try:
            info = os.stat(self.path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return
        self._check_file(info)


def _token_bytes(token: str) -> bytes:
    if len(token) > MAX_TOKEN_BYTES:
        raise CredentialStoreError("Gmail credential JSON exceeds the size limit.")
    try:
        data = token.encode("utf-8")
    except UnicodeError:
        raise CredentialStoreError("Gmail credentials must be a UTF-8 JSON object.") from None
    if len(data) > MAX_TOKEN_BYTES:
        raise CredentialStoreError("Gmail credential JSON exceeds the size limit.")
    try:
        info = json.loads(token)
    except (ValueError, RecursionError):
        raise CredentialStoreError(
            "Gmail credentials must be a bounded UTF-8 JSON object."
        ) from None
    if not isinstance(info, dict):
        raise CredentialStoreError("Gmail credentials must be a JSON object.")
    return data


def get_credential_store(service: str, account: str) -> CredentialStore:
    """Select via Settings (environment/.env), without importing keyring for file.

    GMAIL_CREDENTIAL_STORE defaults to keyring. Selecting file requires an
    explicit absolute GMAIL_CREDENTIAL_FILE; no fallback or migration occurs.
    """
    from backend_scout.config import Settings

    try:
        settings = Settings()
    except ValueError:
        raise CredentialStoreError(
            "Invalid Gmail credential settings; store must be keyring or file."
        ) from None
    provider = settings.gmail_credential_store
    if provider == "keyring":
        return KeyringCredentialStore(service, account)
    if provider == "file":
        path = settings.gmail_credential_file
        if path is None:
            raise CredentialStoreError("File storage requires GMAIL_CREDENTIAL_FILE.")
        return FileCredentialStore(path)
    raise CredentialStoreError("GMAIL_CREDENTIAL_STORE must be keyring or file.")
