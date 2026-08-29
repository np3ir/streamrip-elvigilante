"""Mass-library expansion and resumable planning primitives."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import AsyncIterator, Callable, Iterable

import click

from .client.candidate import track_identity
from .config import Config
from .db import Database
from .filepath_utils import clean_filepath
from .media.artwork import download_artwork
from .media.lyrics import fetch_lrc
from .media.media import Pending
from .media.track import Track
from .metadata import AlbumMetadata, ArtistMetadata, TrackMetadata

SUPPORTED_EXPANSIONS = ("tracks", "albums", "artists")
logger = logging.getLogger("streamrip")


@dataclass(frozen=True, slots=True)
class LibraryTrack:
    source: str
    source_id: str
    title: str
    artist: str
    isrc: str | None
    album_id: str | None = None
    album_title: str | None = None

    @property
    def recording_key(self) -> str:
        isrc = (self.isrc or "").strip().upper()
        return f"isrc:{isrc}" if isrc else f"{self.source}:track:{self.source_id}"

    def job_key(self, expansion: str) -> str:
        if expansion == "tracks":
            return self.recording_key
        return f"album:{self.album_id or 'unknown'}:track:{self.source_id}"


@dataclass(slots=True)
class PendingLibraryTrack(Pending):
    """Reference metadata combined with audio from the selected service."""

    reference_id: str
    reference_client: object
    audio_id: str
    audio_client: object
    audio_quality: int
    config: Config
    db: Database
    completion_callback: Callable[[], None] | None = None
    failure_callback: Callable[[], None] | None = None

    async def resolve(self) -> Track | None:
        reference_source = self.reference_client.source
        response = await self.reference_client.get_metadata(self.reference_id, "track")
        separator = self.config.session.metadata.artist_separator
        album = await self._canonical_album(response, separator)
        metadata = TrackMetadata.from_resp(album, reference_source, response, separator)
        if metadata is None:
            return None

        album_folder = self._canonical_folder(album, reference_source)
        folder = album_folder
        if (
            self.config.session.downloads.disc_subdirectories
            and album.disctotal > 1
        ):
            folder = os.path.join(folder, f"Disc {metadata.discnumber}")
        os.makedirs(folder, exist_ok=True)

        downloadable = await self.audio_client.get_downloadable(
            self.audio_id, self.audio_quality
        )
        cover_path, _ = await download_artwork(
            self.reference_client.session,
            album_folder,
            album.covers,
            self.config.session.artwork,
            for_playlist=False,
        )
        lyrics = await fetch_lrc(self.reference_client, self.reference_id, self.config)
        return Track(
            metadata,
            downloadable,
            self.config,
            folder,
            cover_path,
            self.db,
            lrc_content=lyrics,
            completion_callback=self.completion_callback,
            failure_callback=self.failure_callback,
            failure_id=self.audio_id,
        )

    async def _canonical_album(
        self, response: dict, artist_separator: str
    ) -> AlbumMetadata:
        source = self.reference_client.source
        album_id = (response.get("album") or {}).get("id")
        if album_id:
            try:
                album_response = await self.reference_client.get_metadata(
                    str(album_id), "album"
                )
                full_album = AlbumMetadata.from_album_resp(
                    album_response, source, artist_separator
                )
                if full_album is not None:
                    return full_album
            except Exception as error:
                # A summarized track remains a safe metadata fallback when a
                # region-specific full album endpoint is unavailable.
                logger.debug(
                    "Could not resolve canonical %s album %s: %s",
                    source,
                    album_id,
                    error,
                )
        album = AlbumMetadata.from_track_resp(
            response, source, artist_separator
        )
        if album is None:
            raise ValueError(f"No canonical album metadata for {self.reference_id}")
        return album

    def _canonical_folder(self, album: AlbumMetadata, source: str) -> str:
        config = self.config.session
        parent = config.downloads.folder
        if config.downloads.source_subdirectories:
            parent = os.path.join(parent, source.capitalize())
        formatted = album.format_folder_path(config.filepaths.folder_format)
        folder = clean_filepath(formatted, config.filepaths.restrict_characters)
        return os.path.join(parent, folder[:150].rstrip())


def _items(response: dict) -> list[dict]:
    tracks = response.get("tracks") or []
    if isinstance(tracks, dict):
        tracks = tracks.get("items") or tracks.get("data") or []
    return [item for item in tracks if isinstance(item, dict)]


def _library_track(source: str, item: dict) -> LibraryTrack | None:
    identity = track_identity(source, item)
    if not identity.source_id:
        return None
    album = item.get("album") or {}
    return LibraryTrack(
        source=source,
        source_id=identity.source_id,
        title=identity.title,
        artist=identity.artist,
        isrc=identity.isrc,
        album_id=str(album.get("id")) if album.get("id") is not None else None,
        album_title=album.get("title"),
    )


def _unique_ids(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _album_ids(items: Iterable[dict]) -> list[str]:
    return _unique_ids(
        str(album["id"])
        for item in items
        if isinstance((album := item.get("album")), dict)
        and album.get("id") is not None
    )


def _artist_ids(items: Iterable[dict]) -> list[str]:
    ids: list[str] = []
    for item in items:
        artists = item.get("artists") or []
        if not artists and isinstance(item.get("artist"), dict):
            artists = [item["artist"]]
        for artist in artists:
            if isinstance(artist, dict) and artist.get("id") is not None:
                ids.append(str(artist["id"]))
    return _unique_ids(ids)


async def _album_tracks(client, album_id: str) -> list[dict]:
    return _items(await client.get_metadata(album_id, "album"))


async def iter_library_tracks(
    client,
    media_type: str,
    item_id: str,
    expansion: str,
) -> AsyncIterator[LibraryTrack]:
    """Yield a bounded stream of tracks for direct or expanded resources."""

    source = client.source
    if media_type == "track":
        raw = await client.get_metadata(item_id, "track")
        if track := _library_track(source, raw):
            yield track
        return

    response = await client.get_metadata(item_id, media_type)
    if media_type in {"album", "mix"}:
        for item in _items(response):
            if track := _library_track(source, item):
                yield track
        return

    if media_type == "artist":
        artist = ArtistMetadata.from_resp(response, source)
        for album_id in _unique_ids(str(value) for value in artist.album_ids()):
            for item in await _album_tracks(client, album_id):
                if track := _library_track(source, item):
                    yield track
        return

    if media_type != "playlist":
        raise ValueError(f"Unsupported library resource type: {media_type}")

    playlist_items = _items(response)
    if expansion == "tracks":
        for item in playlist_items:
            if track := _library_track(source, item):
                yield track
        return

    resource_ids = (
        _album_ids(playlist_items)
        if expansion == "albums"
        else _artist_ids(playlist_items)
    )
    seen_album_ids: set[str] = set()
    for resource_id in resource_ids:
        if expansion == "albums":
            album_ids = [resource_id]
        else:
            artist_response = await client.get_metadata(resource_id, "artist")
            artist = ArtistMetadata.from_resp(artist_response, source)
            album_ids = _unique_ids(str(value) for value in artist.album_ids())
        for album_id in album_ids:
            if album_id in seen_album_ids:
                continue
            seen_album_ids.add(album_id)
            for item in await _album_tracks(client, album_id):
                if track := _library_track(source, item):
                    yield track


def library_job_signature(fields: dict) -> str:
    payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


class LibraryCheckpoint:
    """Atomic best-effort checkpoint for successfully planned track keys."""

    def __init__(self, signature: str, directory: Path | None = None):
        base = directory or Path(click.get_app_dir("streamrip")) / "library-resume"
        self.path = base / f"{signature}.json"
        self.completed: set[str] = set()

    def load(self) -> "LibraryCheckpoint":
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.completed = {str(key) for key in data.get("completed", [])}
        except (FileNotFoundError, OSError, ValueError, TypeError):
            self.completed = set()
        return self

    def is_done(self, key: str) -> bool:
        return key in self.completed

    def mark_done(self, key: str) -> None:
        if key in self.completed:
            return
        self.completed.add(key)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(
                    {"signature": self.path.stem, "completed": sorted(self.completed)},
                    indent=2,
                ),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError:
            pass


def track_asdict(track: LibraryTrack) -> dict:
    """Stable serialization helper for future library manifests."""

    return asdict(track)
