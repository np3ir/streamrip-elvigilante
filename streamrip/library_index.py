"""Persistent, incremental index of tagged audio files in trusted libraries."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile

from .multisource import AudioQuality

AUDIO_EXTENSIONS = frozenset({".flac", ".m4a", ".mp4", ".mp3", ".ogg", ".opus"})


def default_library_index_path() -> Path:
    import click

    return Path(click.get_app_dir("streamrip")) / "library-index.db"


def normalize_isrc(value: object) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return "".join(str(value or "").strip().upper().split())


@dataclass(frozen=True, slots=True)
class IndexedTrack:
    path: str
    root: str
    isrc: str
    size: int
    mtime_ns: int
    codec: str
    lossless: bool
    bit_depth: int | None
    sample_rate_hz: int | None

    @property
    def quality(self) -> AudioQuality:
        return AudioQuality(
            codec=self.codec,
            lossless=self.lossless,
            bit_depth=self.bit_depth,
            sample_rate_hz=self.sample_rate_hz,
        )


@dataclass(frozen=True, slots=True)
class ScanResult:
    root: str
    discovered: int
    indexed: int
    unchanged: int
    untagged: int
    removed: int
    failed: int


class LibraryIndex:
    """SQLite-backed ISRC index whose records are verified against the filesystem."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_library_index_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._create()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _create(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS roots (
                    path TEXT PRIMARY KEY,
                    last_scan_ns INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tracks (
                    path TEXT PRIMARY KEY,
                    root TEXT NOT NULL,
                    isrc TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    codec TEXT NOT NULL,
                    lossless INTEGER NOT NULL,
                    bit_depth INTEGER,
                    sample_rate_hz INTEGER
                );
                CREATE INDEX IF NOT EXISTS tracks_isrc ON tracks(isrc);
                CREATE INDEX IF NOT EXISTS tracks_root ON tracks(root);
                """
            )

    @staticmethod
    def _root(path: str | Path) -> Path:
        root = Path(path).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(os.fspath(root))
        return root

    @staticmethod
    def _read(path: Path, root: Path, stat: os.stat_result) -> IndexedTrack | None:
        audio = MutagenFile(path, easy=True)
        if audio is None or getattr(audio, "tags", None) is None:
            return None
        tags = audio.tags
        isrc = normalize_isrc(tags.get("isrc") or tags.get("ISRC"))
        if not isrc:
            return None
        info = getattr(audio, "info", None)
        sample_rate = getattr(info, "sample_rate", None)
        bit_depth = getattr(info, "bits_per_sample", None)
        codec = path.suffix.lower().lstrip(".") or type(audio).__name__.lower()
        lossless = codec in {"flac", "alac"} or bool(bit_depth)
        return IndexedTrack(
            path=os.fspath(path),
            root=os.fspath(root),
            isrc=isrc,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            codec=codec,
            lossless=lossless,
            bit_depth=int(bit_depth) if bit_depth else None,
            sample_rate_hz=int(sample_rate) if sample_rate else None,
        )

    @staticmethod
    def _values(track: IndexedTrack) -> tuple:
        return (
            track.path,
            track.root,
            track.isrc,
            track.size,
            track.mtime_ns,
            track.codec,
            int(track.lossless),
            track.bit_depth,
            track.sample_rate_hz,
        )

    def scan(
        self,
        root: str | Path,
        *,
        workers: int = 8,
        progress: Callable[[int, int], None] | None = None,
    ) -> ScanResult:
        if workers < 1:
            raise ValueError("workers must be at least 1")
        canonical_root = self._root(root)
        root_text = os.fspath(canonical_root)
        with self._connect() as connection:
            cached = {
                row["path"]: (row["size"], row["mtime_ns"])
                for row in connection.execute(
                    "SELECT path, size, mtime_ns FROM tracks WHERE root=?", (root_text,)
                )
            }
            seen: set[str] = set()
            discovered = indexed = unchanged = untagged = failed = 0
            changed: list[tuple[Path, os.stat_result]] = []
            def walk_error(_error: OSError) -> None:
                nonlocal failed
                failed += 1

            for directory, _subdirs, filenames in os.walk(
                canonical_root, onerror=walk_error
            ):
                for filename in filenames:
                    path = Path(directory, filename)
                    if path.suffix.lower() not in AUDIO_EXTENSIONS:
                        continue
                    discovered += 1
                    if progress is not None and discovered % 500 == 0:
                        progress(discovered, 0)
                    path_text = os.fspath(path)
                    seen.add(path_text)
                    try:
                        stat = path.stat()
                        if cached.get(path_text) == (stat.st_size, stat.st_mtime_ns):
                            unchanged += 1
                            continue
                        changed.append((path, stat))
                    except OSError:
                        failed += 1

            def inspect(item):
                path, stat = item
                try:
                    return path, self._read(path, canonical_root, stat), None
                except Exception as error:
                    return path, None, error

            with ThreadPoolExecutor(max_workers=workers) as executor:
                inspected = executor.map(inspect, changed)
                for position, (path, track, error) in enumerate(inspected, start=1):
                    path_text = os.fspath(path)
                    if error is not None:
                        failed += 1
                    elif track is None:
                        connection.execute("DELETE FROM tracks WHERE path=?", (path_text,))
                        untagged += 1
                    else:
                        connection.execute(
                            """INSERT INTO tracks
                            (path, root, isrc, size, mtime_ns, codec, lossless,
                             bit_depth, sample_rate_hz)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(path) DO UPDATE SET
                             root=excluded.root, isrc=excluded.isrc,
                             size=excluded.size, mtime_ns=excluded.mtime_ns,
                             codec=excluded.codec, lossless=excluded.lossless,
                             bit_depth=excluded.bit_depth,
                             sample_rate_hz=excluded.sample_rate_hz""",
                            self._values(track),
                        )
                        indexed += 1
                    if position % 100 == 0:
                        connection.commit()
                    if progress is not None:
                        progress(position, len(changed))

            removed = 0
            for old_path in set(cached) - seen:
                connection.execute("DELETE FROM tracks WHERE path=?", (old_path,))
                removed += 1
            connection.execute(
                """INSERT INTO roots(path, last_scan_ns) VALUES (?, ?)
                ON CONFLICT(path) DO UPDATE SET last_scan_ns=excluded.last_scan_ns""",
                (root_text, __import__("time").time_ns()),
            )
            for (registered,) in connection.execute("SELECT path FROM roots").fetchall():
                if registered == root_text:
                    continue
                try:
                    Path(registered).relative_to(canonical_root)
                except ValueError:
                    continue
                connection.execute("DELETE FROM roots WHERE path=?", (registered,))
        return ScanResult(
            root_text, discovered, indexed, unchanged, untagged, removed, failed
        )

    def update(
        self,
        *,
        workers: int = 8,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[ScanResult]:
        with self._connect() as connection:
            roots = [row[0] for row in connection.execute("SELECT path FROM roots")]
        return [
            self.scan(root, workers=workers, progress=progress)
            for root in roots
            if Path(root).is_dir()
        ]

    def roots(self) -> list[tuple[str, int]]:
        with self._connect() as connection:
            return [tuple(row) for row in connection.execute(
                "SELECT path, last_scan_ns FROM roots ORDER BY path"
            )]

    def count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])

    def index_file(self, path: str | Path, root: str | Path) -> IndexedTrack | None:
        file_path = Path(path).resolve(strict=True)
        canonical_root = self._root(root)
        file_path.relative_to(canonical_root)
        stat = file_path.stat()
        track = self._read(file_path, canonical_root, stat)
        if track is None:
            return None
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO roots(path, last_scan_ns) VALUES (?, 0)",
                (os.fspath(canonical_root),),
            )
            connection.execute(
                """INSERT OR REPLACE INTO tracks
                (path, root, isrc, size, mtime_ns, codec, lossless,
                 bit_depth, sample_rate_hz) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._values(track),
            )
        return track

    def find(self, isrc: str) -> list[IndexedTrack]:
        normalized = normalize_isrc(isrc)
        if not normalized:
            return []
        valid: list[IndexedTrack] = []
        stale: list[str] = []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tracks WHERE isrc=?", (normalized,)
            ).fetchall()
            for row in rows:
                path = Path(row["path"])
                try:
                    stat = path.stat()
                except OSError:
                    stale.append(row["path"])
                    continue
                if stat.st_size != row["size"] or stat.st_mtime_ns != row["mtime_ns"]:
                    try:
                        refreshed = self._read(path, Path(row["root"]), stat)
                    except Exception:
                        refreshed = None
                    if refreshed is None:
                        stale.append(row["path"])
                        continue
                    connection.execute(
                        """INSERT OR REPLACE INTO tracks
                        (path, root, isrc, size, mtime_ns, codec, lossless,
                         bit_depth, sample_rate_hz) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        self._values(refreshed),
                    )
                    if refreshed.isrc == normalized:
                        valid.append(refreshed)
                    continue
                valid.append(
                    IndexedTrack(
                        path=row["path"], root=row["root"], isrc=row["isrc"],
                        size=row["size"], mtime_ns=row["mtime_ns"], codec=row["codec"],
                        lossless=bool(row["lossless"]), bit_depth=row["bit_depth"],
                        sample_rate_hz=row["sample_rate_hz"],
                    )
                )
            if stale:
                connection.executemany("DELETE FROM tracks WHERE path=?", ((p,) for p in stale))
        return valid

    def best(self, isrc: str) -> IndexedTrack | None:
        matches = self.find(isrc)
        return max(matches, key=lambda item: item.quality.rank, default=None)

    def duplicates(self) -> list[tuple[str, list[IndexedTrack]]]:
        with self._connect() as connection:
            isrcs = [row[0] for row in connection.execute(
                "SELECT isrc FROM tracks GROUP BY isrc HAVING COUNT(*) > 1 ORDER BY isrc"
            )]
        result = []
        for isrc in isrcs:
            matches = self.find(isrc)
            if len(matches) > 1:
                result.append((isrc, matches))
        return result


def quality_at_least(existing: AudioQuality, requested: AudioQuality) -> bool:
    """Return whether an indexed file makes a new winner download redundant."""
    spatial_codecs = {"eac3", "ec-3", "ac3", "ac-3"}
    if (
        not requested.spatial
        and existing.codec.casefold() in spatial_codecs
        and requested.codec.casefold() not in spatial_codecs
    ):
        return False
    if requested.lossless and not existing.lossless:
        return False
    if existing.lossless:
        if requested.bit_depth is not None and (
            existing.bit_depth is None or existing.bit_depth < requested.bit_depth
        ):
            return False
        if requested.sample_rate_hz is not None and (
            existing.sample_rate_hz is None
            or existing.sample_rate_hz < requested.sample_rate_hz
        ):
            return False
        return True
    if requested.bitrate_kbps is not None:
        return (
            existing.bitrate_kbps is not None
            and existing.bitrate_kbps >= requested.bitrate_kbps
        )
    return existing.codec.casefold() == requested.codec.casefold()
