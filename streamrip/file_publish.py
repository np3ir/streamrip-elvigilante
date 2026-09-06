"""Durable, cross-filesystem publication of verified local media files."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import click

RECOVERY_VERSION = 1


def default_recovery_directory() -> Path:
    return Path(click.get_app_dir("streamrip")) / "publish-recovery"


@dataclass(frozen=True, slots=True)
class RecoveryEntry:
    """A verified staging file waiting to be published."""

    id: str
    created_at: str
    staging_path: str
    destination_path: str
    size: int
    sha256: str
    destination_root: str | None = None
    destination_anchor_id: str | None = None
    version: int = RECOVERY_VERSION


class RecoveryError(OSError):
    """A recovery entry is invalid, unsafe, or cannot be processed."""


class PublishError(OSError):
    """The verified staging file could not be published and was retained."""

    def __init__(self, message: str, retained_path: Path):
        super().__init__(message)
        self.retained_path = retained_path
        self.recovery_id: str | None = None
        self.recovery_error: str | None = None

    def __str__(self) -> str:
        detail = f"{self.args[0]}; verified staging retained at {self.retained_path}"
        if self.recovery_id is not None:
            return f"{detail}; recovery ID {self.recovery_id}"
        if self.recovery_error is not None:
            return f"{detail}; recovery registration failed: {self.recovery_error}"
        return detail


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as handle:
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            # Some remote/virtual filesystems do not expose a flush primitive.
            pass


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry_path(entry_id: str, directory: Path) -> Path:
    if not entry_id or any(character not in "0123456789abcdef" for character in entry_id):
        raise RecoveryError("invalid recovery ID")
    return directory / f"{entry_id}.json"


def register_recovery(
    staging_path: str | Path,
    destination_path: str | Path,
    *,
    destination_root: str | Path | None = None,
    destination_anchor_id: str | None = None,
    directory: str | Path | None = None,
) -> RecoveryEntry:
    """Atomically register a verified stage without moving or modifying it."""

    try:
        staging = Path(staging_path).resolve(strict=True)
    except OSError as error:
        raise RecoveryError("staging file is unavailable") from error
    destination = Path(destination_path).resolve(strict=False)
    if not staging.is_file() or staging.stat().st_size <= 0:
        raise RecoveryError("staging file is missing or empty")

    entry = RecoveryEntry(
        id=uuid.uuid4().hex,
        created_at=datetime.now(timezone.utc).isoformat(),
        staging_path=os.fspath(staging),
        destination_path=os.fspath(destination),
        size=staging.stat().st_size,
        sha256=_sha256(staging),
        destination_root=(
            os.fspath(Path(destination_root)) if destination_root is not None else None
        ),
        destination_anchor_id=destination_anchor_id,
    )
    recovery_dir = Path(directory) if directory is not None else default_recovery_directory()
    recovery_dir.mkdir(parents=True, exist_ok=True)
    final = _entry_path(entry.id, recovery_dir)
    temporary = recovery_dir / f".{entry.id}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(asdict(entry), indent=2), encoding="utf-8")
        _fsync_file(temporary)
        os.replace(temporary, final)
        _fsync_directory(recovery_dir)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return entry


def list_recoveries(*, directory: str | Path | None = None) -> list[RecoveryEntry]:
    """Return valid recovery entries in creation order."""

    recovery_dir = Path(directory) if directory is not None else default_recovery_directory()
    if not recovery_dir.is_dir():
        return []
    entries: list[RecoveryEntry] = []
    for path in recovery_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entry = RecoveryEntry(**data)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if path.name == f"{entry.id}.json" and entry.version == RECOVERY_VERSION:
            entries.append(entry)
    return sorted(entries, key=lambda entry: (entry.created_at, entry.id))


def get_recovery(
    entry_id: str, *, directory: str | Path | None = None
) -> RecoveryEntry:
    recovery_dir = Path(directory) if directory is not None else default_recovery_directory()
    path = _entry_path(entry_id.lower(), recovery_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = RecoveryEntry(**data)
    except FileNotFoundError as error:
        raise RecoveryError(f"recovery entry not found: {entry_id}") from error
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RecoveryError(f"invalid recovery entry: {entry_id}") from error
    if entry.id != entry_id.lower() or entry.version != RECOVERY_VERSION:
        raise RecoveryError(f"invalid recovery entry: {entry_id}")
    return entry


def _validate_recovery_stage(entry: RecoveryEntry) -> Path:
    staging = Path(entry.staging_path)
    try:
        size = staging.stat().st_size
    except OSError as error:
        raise RecoveryError("retained staging file is unavailable") from error
    if not staging.is_file() or size != entry.size or _sha256(staging) != entry.sha256:
        raise RecoveryError("retained staging file no longer matches its verified record")
    return staging


def remove_recovery(
    entry_id: str,
    *,
    delete_staging: bool = False,
    directory: str | Path | None = None,
) -> RecoveryEntry:
    """Remove one record, optionally deleting its still-verified staging file."""

    recovery_dir = Path(directory) if directory is not None else default_recovery_directory()
    entry = get_recovery(entry_id, directory=recovery_dir)
    if delete_staging:
        staging = _validate_recovery_stage(entry)
        try:
            staging.unlink()
        except OSError as error:
            raise RecoveryError("could not delete retained staging file") from error
    _entry_path(entry.id, recovery_dir).unlink()
    _fsync_directory(recovery_dir)
    return entry


async def retry_recovery(
    entry_id: str,
    *,
    identity_mode: str = "off",
    directory: str | Path | None = None,
) -> RecoveryEntry:
    """Publish one unchanged retained stage and remove its record on success."""

    recovery_dir = Path(directory) if directory is not None else default_recovery_directory()
    entry = get_recovery(entry_id, directory=recovery_dir)
    staging = await asyncio.to_thread(_validate_recovery_stage, entry)
    if identity_mode == "strict":
        if entry.destination_root is None or entry.destination_anchor_id is None:
            raise RecoveryError("recovery entry has no trusted destination identity")
        from .destination_identity import check_destination

        check_destination(
            entry.destination_root,
            entry.destination_path,
            expected_anchor_id=entry.destination_anchor_id,
        )
    await publish_verified_file(staging, entry.destination_path)
    await asyncio.to_thread(remove_recovery, entry.id, directory=recovery_dir)
    return entry


def _same_volume(source: Path, destination_parent: Path) -> bool:
    try:
        return source.stat().st_dev == destination_parent.stat().st_dev
    except OSError:
        return False


def _publish_sync(source: Path, destination: Path) -> None:
    if not source.is_file() or source.stat().st_size <= 0:
        raise PublishError("staging file is missing or empty", source)
    if not destination.parent.is_dir():
        raise PublishError("destination directory does not exist", source)

    _fsync_file(source)
    if _same_volume(source, destination.parent):
        try:
            os.replace(source, destination)
            _fsync_directory(destination.parent)
            return
        except OSError as error:
            raise PublishError(f"atomic rename failed: {error}", source) from error

    destination_stage = destination.with_name(
        f".{destination.name}.streamrip-part-{uuid.uuid4().hex[:8]}"
    )
    try:
        shutil.copy2(source, destination_stage)
        _fsync_file(destination_stage)
        if (
            destination_stage.stat().st_size != source.stat().st_size
            or _sha256(destination_stage) != _sha256(source)
        ):
            raise OSError("destination copy failed SHA-256 verification")
        os.replace(destination_stage, destination)
        _fsync_directory(destination.parent)
    except OSError as error:
        try:
            destination_stage.unlink()
        except FileNotFoundError:
            pass
        raise PublishError(f"cross-volume publish failed: {error}", source) from error
    else:
        try:
            source.unlink()
        except OSError:
            # Publication is truthful even if best-effort local cleanup fails.
            pass


async def publish_verified_file(source: str | Path, destination: str | Path) -> None:
    """Publish without exposing a partial final file or destroying a prior one."""

    await asyncio.to_thread(_publish_sync, Path(source), Path(destination))
