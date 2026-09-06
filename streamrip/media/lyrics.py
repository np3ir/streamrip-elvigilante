import asyncio
import json
import logging
from collections.abc import Iterable

import aiohttp

from ..client import Client
from ..config import Config
from ..exceptions import NonStreamableError

logger = logging.getLogger("streamrip")


async def fetch_lrc(client: Client, track_id: str, config: Config) -> str | None:
    """Fetch LRC lyrics from the client if the feature is enabled in config.

    Returns the LRC content as a string, or ``None`` when lyrics are not
    configured, the client does not support lyrics, or the request fails.
    Shared by PendingTrack, PendingSingle, and PendingPlaylistTrack.
    """
    if not config.session.lyrics.save_lrc:
        return None
    if not hasattr(client, "get_lyrics"):
        return None
    try:
        return await client.get_lyrics(track_id)  # type: ignore[attr-defined]
    except (
        aiohttp.ClientError,
        aiohttp.ClientResponseError,
        asyncio.TimeoutError,
        json.JSONDecodeError,
        NonStreamableError,
    ) as e:
        logger.debug("Could not fetch lyrics for track %s: %s", track_id, e)
        return None


async def fetch_lrc_from_sources(
    sources: Iterable[tuple[Client, str]], config: Config
) -> str | None:
    """Return lyrics from the first capable matched source that has them."""
    if not config.session.lyrics.save_lrc:
        return None
    seen: set[tuple[str, str]] = set()
    for client, track_id in sources:
        key = (str(getattr(client, "source", "")), str(track_id))
        if key in seen:
            continue
        seen.add(key)
        lyrics = await fetch_lrc(client, str(track_id), config)
        if lyrics:
            logger.debug("Lyrics resolved from %s track %s", key[0], key[1])
            return lyrics
    return None
