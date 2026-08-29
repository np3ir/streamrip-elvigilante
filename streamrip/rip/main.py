import asyncio
import json
import logging
import os
import platform
import re
import sys

import aiofiles

from .. import db
from ..client import Client, DeezerClient, QobuzClient, SoundcloudClient, TidalClient
from ..config import Config
from ..console import console
from ..exceptions import AuthenticationError, MissingCredentialsError
from ..media import (
    PendingAlbum,
    PendingArtist,
    PendingLabel,
    PendingPlaylist,
    PendingSingle,
    remove_artwork_tempdirs,
)
from ..metadata import SearchResults
from ..progress import clear_progress
from .parse_url import parse_url
from .prompter import get_prompter

logger = logging.getLogger("streamrip")

if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class Main:
    def __init__(self, config: Config):
        self.config = config
        target_folder = config.session.downloads.folder
        database_config = config.session.database
        db_path = database_config.downloads_path or os.path.join(
            target_folder, "downloads.db"
        )
        failed_db_path = database_config.failed_downloads_path or os.path.join(
            target_folder, "failed_downloads.db"
        )

        # Initialize Clients
        self.clients: dict[str, Client] = {
            "qobuz": QobuzClient(config),
            "tidal": TidalClient(config),
            "deezer": DeezerClient(config),
            "soundcloud": SoundcloudClient(config),
        }

        # Initialize Database with correct paths
        if database_config.downloads_enabled:
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
            downloads_db = db.Downloads(db_path)
        else:
            downloads_db = db.Dummy()
        if database_config.failed_downloads_enabled:
            os.makedirs(os.path.dirname(os.path.abspath(failed_db_path)), exist_ok=True)
            failed_downloads_db = db.Failed(failed_db_path)
        else:
            failed_downloads_db = db.Dummy()

        # ISRC cross-source deduplication DB — same folder as downloads DB.
        isrc_db_path = os.path.join(
            os.path.dirname(os.path.abspath(db_path)), "isrc_downloaded.db"
        )
        isrc_db = (
            db.DownloadedISRCs(isrc_db_path)
            if database_config.isrc_enabled is True
            else db.Dummy()
        )

        self.database = db.Database(downloads_db, failed_downloads_db, isrc_db)
        # -----------------------------------------------------------

        self.queue = asyncio.Queue()
        self.producer_tasks = []
        self.skipped_items = 0  # count of items skipped due to errors

    async def add(self, url: str):
        # Background streaming for Tidal artists
        tidal_artist_match = re.search(r"tidal\.com.*/artist/(\d+)", url)

        if tidal_artist_match:
            artist_id = tidal_artist_match.group(1)
            task = asyncio.create_task(self._background_search_artist(artist_id))
            self.producer_tasks.append(task)
            return

        parsed = parse_url(url)
        if parsed is None:
            raise Exception(f"Unable to parse url {url}")
        client = await self.get_logged_in_client(parsed.source)
        item = await parsed.into_pending(client, self.config, self.database)
        await self.queue.put(item)

    async def _background_search_artist(self, artist_id: str):
        try:
            client = await self.get_logged_in_client("tidal")

            # Fetch Artist Name for better UI
            display_name = artist_id
            try:
                artist_meta = await client.get_metadata(artist_id, "artist")
                if isinstance(artist_meta, dict) and "name" in artist_meta:
                    display_name = artist_meta["name"]
            except Exception:
                pass

            console.print(f"[green]Streaming started: Searching releases for {display_name}...[/green]")

            # Avoid duplicates
            seen_albums: set[str] = set()
            total_albums = 0
            skipped_duplicates = 0

            async for album_batch in client.get_artist_albums_stream(artist_id):
                count = 0
                for album in album_batch:
                    album_id = str(album.get("id")) if isinstance(album, dict) else ""
                    if not album_id:
                        continue

                    if album_id in seen_albums:
                        skipped_duplicates += 1
                        continue

                    seen_albums.add(album_id)
                    await self.queue.put(PendingAlbum(album_id, client, self.config, self.database))
                    count += 1
                    total_albums += 1

                if count > 0:
                    console.print(
                        f"[dim]>> Queue fed: +{count} albums (total: {total_albums}, skipped {skipped_duplicates} duplicates)[/dim]"
                    )

            console.print(f"[green]✓ Finished streaming {display_name}: {total_albums} unique albums found[/green]")

        except Exception:
            logger.debug("Error in background search", exc_info=True)

    async def add_by_id(self, source: str, media_type: str, id: str):
        client = await self.get_logged_in_client(source)
        await self._queue_by_id(client, media_type, id)

    async def add_all_by_id(self, info: list[tuple[str, str, str]]):
        """Queue IDs while logging in to each source at most once."""

        clients = {
            source: await self.get_logged_in_client(source)
            for source in dict.fromkeys(source for source, _, _ in info)
        }
        for source, media_type, item_id in info:
            await self._queue_by_id(clients[source], media_type, item_id)

    async def _queue_by_id(self, client: Client, media_type: str, item_id: str):
        if media_type == "track":
            item = PendingSingle(item_id, client, self.config, self.database)
        elif media_type == "album":
            item = PendingAlbum(item_id, client, self.config, self.database)
        elif media_type == "playlist":
            item = PendingPlaylist(item_id, client, self.config, self.database)
        elif media_type == "label":
            item = PendingLabel(item_id, client, self.config, self.database)
        elif media_type == "artist":
            item = PendingArtist(item_id, client, self.config, self.database)
        else:
            raise Exception(media_type)
        await self.queue.put(item)

    async def add_all(self, urls: list[str]):
        for url in urls:
            try:
                await self.add(url)
            except Exception as e:
                console.print(f"[red]Error adding {url}: {e}[/red]")

    async def resolve(self):
        pass

    async def rip(self):
        workers = [asyncio.create_task(self.worker_loop(i)) for i in range(4)]
        if self.producer_tasks:
            await asyncio.gather(*self.producer_tasks)

        await self.queue.join()

        for w in workers:
            w.cancel()

        # Clean end summary
        if self.skipped_items > 5:
            console.print(f"[yellow]⚠ Skipped {self.skipped_items} item(s) due to metadata errors.[/yellow]")

        clear_progress()

    async def search_interactive(self, source: str, media_type: str, query: str):
        """Search and queue the items explicitly selected in a terminal menu."""

        client = await self.get_logged_in_client(source)
        limit = self.config.session.cli.max_search_results
        with console.status(f"[bold]Searching {source}", spinner="dots"):
            pages = await client.search(media_type, query, limit=limit)
            if not pages:
                console.print(f"[red]No search results found for query {query}")
                return
            results = SearchResults.from_pages(source, media_type, pages)

        if not results.results:
            console.print(f"[red]No search results found for query {query}")
            return

        if platform.system() == "Windows":
            from pick import pick

            selected = pick(
                results.results,
                title=(
                    f"{source.capitalize()} {media_type} search.\n"
                    "Press SPACE to select, RETURN to download, CTRL-C to exit."
                ),
                multiselect=True,
                min_selection_count=1,
            )
            if not selected:
                console.print("[yellow]No items chosen. Exiting.")
                return
            choices = [item for item, _ in selected]
        else:
            from simple_term_menu import TerminalMenu

            menu = TerminalMenu(
                results.summaries(),
                preview_command=results.preview,
                preview_size=0.5,
                title=(
                    f"Results for {media_type} '{query}' from {source.capitalize()}\n"
                    "SPACE - select, ENTER - download, ESC - exit"
                ),
                cycle_cursor=True,
                clear_screen=True,
                multi_select=True,
            )
            chosen = menu.show()
            if chosen is None:
                console.print("[yellow]No items chosen. Exiting.")
                return
            choices = results.get_choices(chosen)

        await self.add_all_by_id(
            [(source, item.media_type(), item.id) for item in choices]
        )

    async def search_take_first(self, source: str, media_type: str, query: str):
        """Queue only the first normalized search result."""

        client = await self.get_logged_in_client(source)
        with console.status(f"[bold]Searching {source}", spinner="dots"):
            pages = await client.search(media_type, query, limit=1)
        if not pages:
            console.print(f"[red]No search results found for query {query}")
            return

        results = SearchResults.from_pages(source, media_type, pages)
        if not results.results:
            console.print(f"[red]No search results found for query {query}")
            return
        first = results.results[0]
        await self.add_by_id(source, first.media_type(), first.id)

    async def search_output_file(
        self, source: str, media_type: str, query: str, filepath: str, limit: int
    ):
        """Write normalized search results as an importable JSON list."""

        client = await self.get_logged_in_client(source)
        with console.status(f"[bold]Searching {source}", spinner="dots"):
            pages = await client.search(media_type, query, limit=limit)
        if not pages:
            console.print(f"[red]No search results found for query {query}")
            return

        results = SearchResults.from_pages(source, media_type, pages)
        if not results.results:
            console.print(f"[red]No search results found for query {query}")
            return
        contents = json.dumps(results.as_list(source), indent=4, ensure_ascii=False)
        async with aiofiles.open(filepath, "w", encoding="utf-8") as output:
            await output.write(contents)
        console.print(
            f"Wrote [purple]{len(results.results)}[/purple] results to "
            f"[cyan]{filepath}[/cyan] as JSON!"
        )

    async def worker_loop(self, worker_id: int):
        while True:
            pending_item = await self.queue.get()
            try:
                media_item = await pending_item.resolve()
                if media_item is None:
                    self.skipped_items += 1
                    # DEBUG: identify what object was skipped
                    pid = getattr(pending_item, "id", None)
                    src = getattr(pending_item, "client", None)
                    src_name = getattr(src, "source", None) if src is not None else None
                    logger.debug(
                        f"[worker {worker_id}] resolve() returned None; "
                        f"pending_type={type(pending_item).__name__} id={pid} source={src_name}"
                    )
                    continue

                await media_item.rip()

            except Exception:
                self.skipped_items += 1
                # DEBUG: identify the exact pending object that caused the exception
                pid = getattr(pending_item, "id", None)
                src = getattr(pending_item, "client", None)
                src_name = getattr(src, "source", None) if src is not None else None
                logger.debug(
                    f"[worker {worker_id}] item failed; "
                    f"pending_type={type(pending_item).__name__} id={pid} source={src_name}",
                    exc_info=True,
                )
                # NOTE: no WARNING here to avoid console/UI spam

            finally:
                self.queue.task_done()

    async def get_logged_in_client(self, source: str, *, prompt_on_missing: bool = True):
        client = self.clients.get(source)
        if client is None:
            raise Exception(f"No client named {source}")
        if not client.logged_in:
            prompter = get_prompter(client, self.config)
            if not prompter.has_creds():
                if not prompt_on_missing:
                    raise MissingCredentialsError(f"{source} credentials are not configured")
                await prompter.prompt_and_login()
                prompter.save()
            else:
                try:
                    await client.login()
                except AuthenticationError:
                    if not prompt_on_missing:
                        raise
                    await prompter.prompt_and_login()
                    prompter.save()
        return client

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        for client in self.clients.values():
            if hasattr(client, "session"):
                await client.session.close()
        try:
            if hasattr(self.database, "downloads") and hasattr(self.database.downloads, "close"):
                self.database.downloads.close()
            if hasattr(self.database, "failed") and hasattr(self.database.failed, "close"):
                self.database.failed.close()
        except Exception:
            pass
        remove_artwork_tempdirs()


def run_main():
    async def main():
        config = Config()
        async with Main(config) as ripper:
            target_urls = sys.argv[1:] if len(sys.argv) > 1 else []
            if target_urls:
                await ripper.add_all(target_urls)
                await ripper.rip()
            else:
                print("No URLs provided.")

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.exception("Error:", exc_info=e)


if __name__ == "__main__":
    run_main()
