"""Opt-in destination identity markers for removable and network libraries."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import click

MARKER_FILENAME = ".streamrip-anchor"
MARKER_FORMAT = "streamrip-destination-anchor"
MARKER_VERSION = 1
MARKER_MAX_BYTES = 4096
STATE_VERSION = 1


class DestinationIdentityError(OSError):
    """A destination is not currently the explicitly trusted volume."""


@dataclass(frozen=True, slots=True)
class DestinationAnchor:
    root_key: str
    root_display: str
    anchor_id: str
    trusted_at: str
    version: int = STATE_VERSION


def state_directory() -> Path:
    return Path(click.get_app_dir("streamrip")) / "destination-anchors"


def root_key(path: str | Path) -> str:
    value = os.fspath(path)
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[len("\\\\?\\UNC\\") :]
    elif value.startswith("\\\\?\\"):
        value = value[len("\\\\?\\") :]
    return os.path.normcase(os.path.normpath(os.path.abspath(value)))


def is_contained(root: str | Path, output: str | Path) -> bool:
    normalized_root = root_key(root)
    normalized_output = root_key(output)
    try:
        return os.path.commonpath([normalized_root, normalized_output]) == normalized_root
    except ValueError:
        return False


def marker_path(root: str | Path) -> Path:
    return Path(root) / MARKER_FILENAME


def _record_path(root: str | Path, directory: Path) -> Path:
    digest = hashlib.sha256(root_key(root).encode("utf-8")).hexdigest()
    return directory / f"{digest}.json"


def _read_marker(root: str | Path) -> str:
    path = marker_path(root)
    try:
        if path.is_symlink():
            raise DestinationIdentityError("destination marker is a symlink")
        raw = path.read_bytes()
    except FileNotFoundError as error:
        raise DestinationIdentityError("destination marker is absent") from error
    except OSError as error:
        raise DestinationIdentityError("destination marker is unreadable") from error
    if len(raw) > MARKER_MAX_BYTES:
        raise DestinationIdentityError("destination marker is oversized")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DestinationIdentityError("destination marker is invalid") from error
    anchor_id = data.get("anchor_id") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict)
        or data.get("format") != MARKER_FORMAT
        or data.get("version") != MARKER_VERSION
        or not isinstance(anchor_id, str)
        or not anchor_id
    ):
        raise DestinationIdentityError("destination marker is invalid")
    return anchor_id


def _read_record(root: str | Path, directory: Path) -> DestinationAnchor:
    try:
        data = json.loads(_record_path(root, directory).read_text(encoding="utf-8"))
        record = DestinationAnchor(**data)
    except FileNotFoundError as error:
        raise DestinationIdentityError("destination is not trusted on this machine") from error
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise DestinationIdentityError("local destination trust record is invalid") from error
    if record.version != STATE_VERSION or record.root_key != root_key(root):
        raise DestinationIdentityError("local destination trust record is invalid")
    return record


def _write_record(record: DestinationAnchor, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    final = _record_path(record.root_key, directory)
    temporary = directory / f".{final.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(asdict(record), handle, indent=2)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temporary, final)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def trust_destination(
    root: str | Path,
    *,
    adopt_existing: bool = False,
    directory: str | Path | None = None,
) -> DestinationAnchor:
    """Create or explicitly adopt a destination-side identity marker."""

    root_path = Path(root)
    if not root_path.is_dir():
        raise DestinationIdentityError("destination root does not exist")
    marker = marker_path(root_path)
    if adopt_existing:
        anchor_id = _read_marker(root_path)
    else:
        anchor_id = uuid.uuid4().hex
        payload = json.dumps(
            {
                "format": MARKER_FORMAT,
                "version": MARKER_VERSION,
                "anchor_id": anchor_id,
            }
        ).encode("utf-8")
        try:
            descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise DestinationIdentityError(
                "destination marker already exists; use --adopt-existing after verification"
            ) from error
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    record = DestinationAnchor(
        root_key=root_key(root_path),
        root_display=os.fspath(root_path),
        anchor_id=anchor_id,
        trusted_at=datetime.now(timezone.utc).isoformat(),
    )
    _write_record(record, Path(directory) if directory is not None else state_directory())
    return record


def check_destination(
    root: str | Path,
    output: str | Path,
    *,
    expected_anchor_id: str | None = None,
    directory: str | Path | None = None,
) -> DestinationAnchor:
    if not is_contained(root, output):
        raise DestinationIdentityError("output is outside the trusted destination root")
    state_dir = Path(directory) if directory is not None else state_directory()
    record = _read_record(root, state_dir)
    marker_id = _read_marker(root)
    if marker_id != record.anchor_id or (
        expected_anchor_id is not None and marker_id != expected_anchor_id
    ):
        raise DestinationIdentityError("destination identity does not match")
    return record


def forget_destination(
    root: str | Path, *, directory: str | Path | None = None
) -> bool:
    """Forget local trust without changing the destination-side marker."""

    state_dir = Path(directory) if directory is not None else state_directory()
    try:
        _record_path(root, state_dir).unlink()
    except FileNotFoundError:
        return False
    return True


def configured_root(config, output: str | Path) -> Path:
    candidates = [
        Path(value)
        for value in (
            config.session.downloads.folder,
            config.session.downloads.playlist_folder,
        )
        if value and is_contained(value, output)
    ]
    if not candidates:
        raise DestinationIdentityError("no configured destination root contains output")
    return max(candidates, key=lambda path: len(root_key(path)))


def guard_configured_write(config, output: str | Path) -> DestinationAnchor | None:
    mode = getattr(config.session.downloads, "destination_identity", "off")
    if mode == "off":
        return None
    if mode != "strict":
        raise DestinationIdentityError(f"invalid destination identity mode: {mode}")
    root = configured_root(config, output)
    return check_destination(root, output)
