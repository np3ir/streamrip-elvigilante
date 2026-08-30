"""Durable, cross-filesystem publication of verified local media files."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import uuid
from pathlib import Path


class PublishError(OSError):
    """The verified staging file could not be published and was retained."""

    def __init__(self, message: str, retained_path: Path):
        super().__init__(f"{message}; verified staging retained at {retained_path}")
        self.retained_path = retained_path


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
