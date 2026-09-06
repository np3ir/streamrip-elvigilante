from types import SimpleNamespace

import pytest

from streamrip.client.candidate import service_candidate, track_identity
from streamrip.client.client import Client
from streamrip.multisource import AudioQuality


def test_qobuz_candidate_uses_resolved_stream_over_album_maximum():
    metadata = {
        "id": 10,
        "title": "Song",
        "performer": {"name": "Artist"},
        "duration": 180,
        "isrc": "USABC1234567",
        "maximum_bit_depth": 24,
        "maximum_sampling_rate": 192,
    }
    delivered = AudioQuality(
        codec="flac", lossless=True, bit_depth=24, sample_rate_hz=96000
    )

    result = service_candidate("qobuz", metadata, SimpleNamespace(quality=delivered))

    assert result.identity.source_id == "10"
    assert result.identity.isrc == "USABC1234567"
    assert result.quality.sample_rate_hz == 96000


def test_deezer_identity_supports_gateway_field_names():
    result = track_identity(
        "deezer",
        {
            "SNG_ID": "20",
            "SNG_TITLE": "Song",
            "artist": {"name": "Artist"},
            "DURATION": "181.4",
            "ISRC": "USABC1234567",
        },
    )

    assert result.source_id == "20"
    assert result.title == "Song"
    assert result.duration_seconds == 181


def test_tidal_candidate_preserves_multiple_artists_and_actual_manifest_quality():
    metadata = {
        "id": "30",
        "title": "Song",
        "artists": [{"name": "Artist A"}, {"name": "Artist B"}],
        "duration": 182,
        "isrc": "USABC1234567",
        "audioQuality": "LOSSLESS",
    }
    delivered = AudioQuality(
        codec="flac", lossless=True, bit_depth=24, sample_rate_hz=192000
    )

    result = service_candidate("tidal", metadata, SimpleNamespace(quality=delivered))

    assert result.identity.artist == "Artist A / Artist B"
    assert result.quality.bit_depth == 24


class FakeClient(Client):
    source = "qobuz"
    max_quality = 4

    async def login(self):
        return None

    async def get_metadata(self, item: str, media_type):
        assert media_type == "track"
        return {
            "id": item,
            "title": "Song",
            "performer": {"name": "Artist"},
            "duration": 180,
            "isrc": "USABC1234567",
        }

    async def search(self, media_type: str, query: str, limit: int = 500):
        return []

    async def get_downloadable(self, item: str, quality: int):
        return SimpleNamespace(
            quality=AudioQuality(
                codec="flac", lossless=True, bit_depth=16, sample_rate_hz=44100
            )
        )


@pytest.mark.asyncio
async def test_client_candidate_api_does_not_download_media_bytes():
    result = await FakeClient().get_candidate("40", 4)

    assert result.identity.source_id == "40"
    assert result.quality.lossless is True
