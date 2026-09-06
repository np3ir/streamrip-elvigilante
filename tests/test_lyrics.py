from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from streamrip.media.lyrics import fetch_lrc_from_sources


@pytest.mark.asyncio
async def test_lyrics_fall_back_from_tidal_to_deezer():
    config = SimpleNamespace(
        session=SimpleNamespace(lyrics=SimpleNamespace(save_lrc=True))
    )
    tidal = SimpleNamespace(source="tidal", get_lyrics=AsyncMock(return_value=None))
    deezer = SimpleNamespace(
        source="deezer", get_lyrics=AsyncMock(return_value="[00:01.00]Found")
    )

    lyrics = await fetch_lrc_from_sources(
        ((tidal, "tidal-id"), (deezer, "deezer-id")), config
    )

    assert lyrics == "[00:01.00]Found"
    tidal.get_lyrics.assert_awaited_once_with("tidal-id")
    deezer.get_lyrics.assert_awaited_once_with("deezer-id")


@pytest.mark.asyncio
async def test_lyrics_disabled_skips_every_provider():
    config = SimpleNamespace(
        session=SimpleNamespace(lyrics=SimpleNamespace(save_lrc=False))
    )
    tidal = SimpleNamespace(source="tidal", get_lyrics=AsyncMock())

    assert await fetch_lrc_from_sources(((tidal, "id"),), config) is None
    tidal.get_lyrics.assert_not_awaited()
