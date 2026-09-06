import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Callable

import aiohttp

from .. import converter
from ..audio_container import normalize_tidal_container
from ..client import Client, Downloadable
from ..config import Config
from ..console import console
from ..db import Database
from ..destination_identity import (
    DestinationIdentityError,
    configured_root,
    guard_configured_write,
)
from ..exceptions import NonStreamableError
from ..file_publish import PublishError

# --- IMPORTAMOS LA NUEVA FUNCIÓN ---
from ..filepath_utils import clean_filename, clean_track_title, truncate_filepath_to_max
from ..metadata import AlbumMetadata, Covers, TrackMetadata, tag_file
from ..progress import add_title, get_progress_callback, remove_title
from .artwork import download_artwork
from .lyrics import fetch_lrc
from .media import Media, Pending
from .semaphore import global_download_semaphore

logger = logging.getLogger("streamrip")



@dataclass(slots=True)
class Track(Media):
    meta: TrackMetadata
    downloadable: Downloadable
    config: Config
    folder: str
    cover_path: str | None
    db: Database
    download_path: str = ""
    is_single: bool = False
    from_playlist: bool = False
    lrc_content: str | None = None
    completion_callback: Callable[[str], None] | None = None
    failure_callback: Callable[[str], None] | None = None
    failure_id: str | None = None

    def _mark_complete(self):
        if self.completion_callback is not None:
            self.completion_callback(self.download_path)

    async def rip(self):
        await self.preprocess()
        if not self.download_path: self._set_download_path()

        # 1. Exact path match
        if os.path.isfile(self.download_path):
            self._save_lrc_sidecar()
            console.print(f"[yellow]Skipped (Exists)[/]: {self.meta.title}")
            if not self.db.downloaded(self.meta.info.id):
                self.db.set_downloaded(self.meta.info.id)
            self.db.set_isrc_downloaded(self.meta.isrc)
            self._mark_complete()
            if self.is_single: remove_title(self.meta.title)
            return

        # 2. Cross-source ISRC check: skip if this recording was already downloaded
        #    from a different platform (Deezer vs Tidal vs Qobuz).
        isrc = self.meta.isrc
        if isrc and self.db.isrc_downloaded(isrc):
            console.print(f"[yellow]Skipped (ISRC, other source)[/]: {self.meta.title}")
            self._mark_complete()
            if self.is_single: remove_title(self.meta.title)
            return

        await self.download()
        # Only postprocess if the file was actually downloaded; download() may return
        # silently after exhausting all retries without creating the file.
        if not os.path.isfile(self.download_path):
            logger.warning(
                "Track '%s' (id=%s) was not downloaded after %d retries; "
                "skipping post-processing.",
                self.meta.title,
                self.meta.info.id,
                self.config.session.downloads.max_retries,
            )
            return
        if self.downloadable.source == "tidal":
            self.download_path = await normalize_tidal_container(
                self.download_path, self.downloadable
            )
        await self.postprocess()
        self._mark_complete()

    async def preprocess(self):
        self._set_download_path()
        guard_configured_write(self.config, self.download_path)
        os.makedirs(self.folder, exist_ok=True)
        if self.is_single: add_title(self.meta.title)

    async def download(self):
        if not self.download_path: self._set_download_path()
        if os.path.isfile(self.download_path): return

        dl_cfg = self.config.session.downloads
        # max_retries controls *additional* attempts beyond the first;
        # the loop runs for attempt in 1..max_retries+1 (inclusive),
        # and attempt max_retries+1 is reserved for the final-failure log.
        max_retries = max(0, getattr(dl_cfg, "max_retries", 3))
        retry_delay = getattr(dl_cfg, "retry_delay", 2.0)
        # Cap the per-attempt wait so high max_retries don't cause multi-minute hangs
        max_wait = getattr(dl_cfg, "max_wait", 60.0)

        async with global_download_semaphore(dl_cfg):
            display_title = self.meta.title
            for attempt in range(1, max_retries + 2):
                try:
                    anchor = guard_configured_write(self.config, self.download_path)
                    destination_root = (
                        os.fspath(configured_root(self.config, self.download_path))
                        if anchor is not None
                        else None
                    )
                    size = await self.downloadable.size()
                    desc = display_title if attempt == 1 else f"{display_title} (retry {attempt - 1})"
                    handle = get_progress_callback(self.config.session.cli.progress_bars, size, desc)
                    with handle as update_fn:
                        if anchor is None:
                            await self.downloadable.download(self.download_path, update_fn)
                        else:
                            await self.downloadable.download(
                                self.download_path,
                                update_fn,
                                destination_root=destination_root,
                                destination_anchor_id=anchor.anchor_id,
                            )
                    return
                except asyncio.CancelledError:
                    # Propagate cancellations so higher-level logic can abort cleanly
                    raise
                except DestinationIdentityError as error:
                    logger.error("Destination write refused: %s", error)
                    self.db.set_failed(
                        self.downloadable.source,
                        "track",
                        self.failure_id or self.meta.info.id,
                    )
                    if self.failure_callback is not None:
                        self.failure_callback(self.download_path)
                    return
                except PublishError as error:
                    logger.error("Verified media could not be published: %s", error)
                    self.db.set_failed(
                        self.downloadable.source,
                        "track",
                        self.failure_id or self.meta.info.id,
                    )
                    if self.failure_callback is not None:
                        self.failure_callback(self.download_path)
                    return
                except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
                    if attempt <= max_retries:
                        wait = min(max_wait, retry_delay * (2 ** (attempt - 1)))
                        logger.warning(
                            "Error downloading '%s' (attempt %d/%d): %s. Retrying in %.1fs...",
                            display_title, attempt, max_retries, e, wait,
                        )
                        await asyncio.sleep(wait)
                    else:
                        logger.exception(
                            "Persistent error downloading '%s' after %d retries.",
                            display_title, max_retries,
                        )
                        self.db.set_failed(
                            self.downloadable.source,
                            "track",
                            self.failure_id or self.meta.info.id,
                        )
                        if self.failure_callback is not None:
                            self.failure_callback(self.download_path)

    def _save_lrc_sidecar(self) -> None:
        """Write fetched lyrics beside the canonical audio file.

        This is intentionally safe to call when the audio already exists so a
        later run can repair a missing or empty sidecar without downloading
        the audio again. A non-empty LRC is user/library data and is never
        overwritten implicitly.
        """
        if not self.lrc_content:
            return
        lrc_path = os.path.splitext(self.download_path)[0] + ".lrc"
        guard_configured_write(self.config, lrc_path)
        try:
            if os.path.isfile(lrc_path) and os.path.getsize(lrc_path) > 0:
                logger.debug("Preserving existing LRC: %s", lrc_path)
                return
        except OSError as error:
            logger.warning("Could not inspect existing LRC file %s: %s", lrc_path, error)
            return
        temporary_path = ""
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{os.path.basename(lrc_path)}.",
                suffix=".tmp",
                dir=os.path.dirname(lrc_path),
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as lrc_file:
                lrc_file.write(self.lrc_content)
                lrc_file.flush()
                os.fsync(lrc_file.fileno())
            os.replace(temporary_path, lrc_path)
            logger.debug("Saved LRC: %s", lrc_path)
        except OSError as error:
            logger.warning("Could not save LRC file %s: %s", lrc_path, error)
        finally:
            if temporary_path and os.path.exists(temporary_path):
                try:
                    os.remove(temporary_path)
                except OSError:
                    logger.warning(
                        "Could not remove temporary LRC file %s", temporary_path
                    )

    async def postprocess(self):
        if self.is_single: remove_title(self.meta.title)
        guard_configured_write(self.config, self.download_path)
        await tag_file(self.download_path, self.meta, self.cover_path)
        if self.config.session.conversion.enabled: await self._convert()
        self._save_lrc_sidecar()
        self.db.set_downloaded(self.meta.info.id)
        self.db.set_isrc_downloaded(self.meta.isrc)
        basename = os.path.basename(self.download_path)
        try:
            console.print(f"[green]Downloaded[/]: {basename}")
        except Exception:
            # Fallback for terminals that can't handle non-ASCII (e.g. Windows cp1252)
            try:
                safe = basename.encode("ascii", errors="replace").decode("ascii")
                console.print(f"[green]Downloaded[/]: {safe}")
            except Exception:
                pass

    async def _convert(self):
        c = self.config.session.conversion
        engine_class = converter.get(c.codec)
        engine = engine_class(filename=self.download_path, sampling_rate=c.sampling_rate, bit_depth=c.bit_depth, remove_source=True)
        await engine.convert()
        self.download_path = engine.final_fn

    def _set_download_path(self):
        c = self.config.session.filepaths
        formatter = "{artist} - {title} {explicit}" if self.from_playlist else c.track_format
        track_path = self.meta.format_track_path(formatter)
        
        # --- USAMOS LA FUNCIÓN CENTRALIZADA ---
        track_path = clean_track_title(track_path, self.meta.artist)
        
        if self.meta.info.explicit and "explicit" not in track_path.lower():
            track_path += " [explicit]"

        track_path = clean_filename(track_path, restrict=c.restrict_characters)
        if c.truncate_to > 0: track_path = track_path[:c.truncate_to]

        raw_path = os.path.join(self.folder, f"{track_path}.{self.downloadable.extension}")
        self.download_path = truncate_filepath_to_max(raw_path)


@dataclass(slots=True)
class PendingTrack(Pending):
    id: str
    album: AlbumMetadata
    client: Client
    config: Config
    folder: str
    db: Database
    cover_path: str | None
    preloaded_data: dict | None = None

    async def resolve(self) -> Track | None:
        source = self.client.source
        try:
            if self.preloaded_data: resp = self.preloaded_data
            else: resp = await self.client.get_metadata(self.id, "track")
        except NonStreamableError: return None

        sep = self.config.session.metadata.artist_separator
        try:
            meta = TrackMetadata.from_resp(self.album, source, resp, sep)
        except Exception as e:
            logger.error("Error parsing track metadata id=%s source=%s: %s", self.id, source, e)
            return None
        if meta is None:
            self.db.set_failed(source, "track", self.id)
            return None

        downloads_config = self.config.session.downloads
        if downloads_config.disc_subdirectories and self.album.disctotal > 1:
            track_folder = os.path.join(self.folder, f"Disc {meta.discnumber}")
        else:
            track_folder = self.folder

        quality = self.config.session.get_source(source).quality
        try: downloadable = await self.client.get_downloadable(self.id, quality)
        except NonStreamableError: return None

        if self.db.downloaded(self.id):
            c = self.config.session.filepaths
            track_path = meta.format_track_path(c.track_format)

            # --- USAMOS LA FUNCIÓN CENTRALIZADA ---
            track_path = clean_track_title(track_path, meta.artist)

            if meta.info.explicit and "explicit" not in track_path.lower():
                track_path += " [explicit]"

            track_path = clean_filename(track_path, restrict=c.restrict_characters)
            if c.truncate_to > 0: track_path = track_path[:c.truncate_to]

            raw_path = os.path.join(track_folder, f"{track_path}.{downloadable.extension}")
            file_path = truncate_filepath_to_max(raw_path)

            if os.path.isfile(file_path):
                console.print(f"[dim]   ↪ Skipped (DB+File): {meta.title}[/dim]")
                return None
            else:
                logger.warning(f"[!] Missing file: {os.path.basename(file_path)}")

        lrc_content = await fetch_lrc(self.client, self.id, self.config)
        return Track(meta, downloadable, self.config, track_folder, self.cover_path, self.db, lrc_content=lrc_content)

@dataclass(slots=True)
class PendingSingle(Pending):
    id: str
    client: Client
    config: Config
    db: Database

    async def resolve(self) -> Track | None:
        try: resp = await self.client.get_metadata(self.id, "track")
        except NonStreamableError: return None
        sep = self.config.session.metadata.artist_separator
        try:
            album = await self._album_metadata(resp, sep)
            meta = TrackMetadata.from_resp(album, self.client.source, resp, sep)
        except Exception as e:
            logger.error("Error parsing single metadata id=%s source=%s: %s", self.id, self.client.source, e)
            return None
        if album is None or meta is None: return None

        config = self.config.session
        quality = getattr(config, self.client.source).quality
        parent = config.downloads.folder
        folder = os.path.join(parent, self._format_folder(album)) if config.filepaths.add_singles_to_folder else parent
        c = config.filepaths
        track_path = meta.format_track_path(c.track_format)
        
        # --- USAMOS LA FUNCIÓN CENTRALIZADA ---
        track_path = clean_track_title(track_path, meta.artist)

        if meta.info.explicit and "explicit" not in track_path.lower():
            track_path += " [explicit]"

        track_path = clean_filename(track_path, restrict=c.restrict_characters)
        if c.truncate_to > 0: track_path = track_path[:c.truncate_to]

        downloadable = await self.client.get_downloadable(self.id, quality)
        raw_path = os.path.join(folder, f"{track_path}.{downloadable.extension}")
        file_path = truncate_filepath_to_max(raw_path)

        if os.path.isfile(file_path):
            console.print(f"[dim]   ↪ Skipped (Exists): {meta.title}[/dim]")
            if not self.db.downloaded(self.id):
                self.db.set_downloaded(self.id)
            return None
        else:
            if self.db.downloaded(self.id): logger.warning(f"[!] Re-downloading: {os.path.basename(file_path)}")
            guard_configured_write(self.config, file_path)
            os.makedirs(folder, exist_ok=True)
            embedded_cover_path = await self._download_cover(album.covers, folder)

            lrc_content = await fetch_lrc(self.client, self.id, self.config)
            return Track(meta, downloadable, self.config, folder, embedded_cover_path, self.db, is_single=True, lrc_content=lrc_content)

    async def _album_metadata(self, track_response: dict, artist_separator: str):
        """Resolve full TIDAL album data so singles get authoritative dates."""

        if self.client.source == "tidal":
            album_id = (track_response.get("album") or {}).get("id")
            if album_id:
                try:
                    album_response = await self.client.get_metadata(str(album_id), "album")
                    return AlbumMetadata.from_tidal(album_response)
                except Exception as error:
                    logger.debug(
                        "Could not resolve full TIDAL album %s: %s",
                        album_id,
                        error,
                    )
        return AlbumMetadata.from_track_resp(
            track_response, self.client.source, artist_separator
        )

    def _format_folder(self, meta: AlbumMetadata) -> str:
        c = self.config.session
        parent = os.path.join(c.downloads.folder, self.client.source.capitalize()) if c.downloads.source_subdirectories else c.downloads.folder
        return os.path.join(parent, meta.format_folder_path(c.filepaths.folder_format))

    async def _download_cover(self, covers: Covers, folder: str) -> str | None:
        embed_path, _ = await download_artwork(self.client.session, folder, covers, self.config.session.artwork, for_playlist=False)
        return embed_path
