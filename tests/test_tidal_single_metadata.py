from unittest.mock import AsyncMock, Mock

import pytest

from streamrip.media.track import PendingSingle


@pytest.mark.asyncio
async def test_tidal_single_fetches_full_album_for_authoritative_release_date():
    track_response = {
        "id": 1,
        "album": {"id": 7, "title": "Embedded album"},
        "streamStartDate": "1970-01-01T00:00:00Z",
    }
    album_response = {
        "id": 7,
        "title": "Real album",
        "releaseDate": "2026-08-28",
        "artist": {"name": "Artist"},
        "audioQuality": "HI_RES_LOSSLESS",
        "numberOfTracks": 1,
        "numberOfVolumes": 1,
        "cover": "01234567-89ab-cdef-0123-456789abcdef",
    }
    client = Mock(source="tidal")
    client.get_metadata = AsyncMock(return_value=album_response)
    pending = PendingSingle("1", client, Mock(), Mock())

    album = await pending._album_metadata(track_response, ", ")

    client.get_metadata.assert_awaited_once_with("7", "album")
    assert album.album == "Real album"
    assert album.release_date == "2026-08-28"
    assert album.year == "2026"
