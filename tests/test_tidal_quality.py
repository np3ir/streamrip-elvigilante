from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from streamrip.client.tidal import TidalClient
from streamrip.multisource import AudioQuality


def playback(audio_quality, codec, **technical):
    return {
        "urls": [f"https://media/{audio_quality.lower()}"],
        "codecs": codec,
        "audioQuality": audio_quality,
        "audioMode": technical.pop("audioMode", "STEREO"),
        **technical,
    }


@pytest.mark.asyncio
async def test_tidal_cascade_requests_each_tier_once_and_keeps_searching_for_flac():
    client = object.__new__(TidalClient)
    client.session = Mock()
    requested = []

    async def request(_path, params=None, **_kwargs):
        requested.append(params["audioquality"])
        if params["audioquality"] in {"HI_RES_LOSSLESS", "HI_RES"}:
            return playback("HIGH", "mp4a.40.2", bitrate=320)
        return playback("LOSSLESS", "flac", bitDepth=16, sampleRate=44100)

    client._api_request = request
    result = await client.get_downloadable("123", quality=4)

    assert requested == ["HI_RES_LOSSLESS", "HI_RES", "LOSSLESS"]
    assert len(requested) == len(set(requested))
    assert result.quality.lossless is True
    assert result.quality.bit_depth == 16


@pytest.mark.asyncio
async def test_tidal_cascade_returns_best_lossy_delivery_when_no_lossless_exists():
    client = object.__new__(TidalClient)
    client.session = Mock()

    async def request(_path, params=None, **_kwargs):
        if params["audioquality"] == "HIGH":
            return playback("HIGH", "mp4a.40.2", bitrate=320)
        return playback("LOW", "mp4a.40.5", bitrate=96)

    client._api_request = request
    result = await client.get_downloadable("123", quality=2)

    assert result.quality.lossless is False
    assert result.quality.bitrate_kbps == 320


@pytest.mark.asyncio
async def test_tidal_cascade_supports_hires_lossless_as_top_tier():
    client = object.__new__(TidalClient)
    client.session = Mock()

    async def request(_path, params=None, **_kwargs):
        assert params["audioquality"] == "HI_RES_LOSSLESS"
        return playback(
            "HI_RES_LOSSLESS",
            "flac",
            bitDepth=24,
            sampleRate=192000,
        )

    client._api_request = request
    result = await client.get_downloadable("123", quality=4)

    assert TidalClient.max_quality == 4
    assert result.quality.lossless is True
    assert result.quality.sample_rate_hz == 192000


@pytest.mark.asyncio
async def test_tidal_lossless_uses_tv_fallback_when_hires_token_degrades_to_aac():
    client = object.__new__(TidalClient)
    client.session = Mock()
    client.allow_lossless_fallback = True

    async def request(_path, params=None, **_kwargs):
        return playback("HIGH", "mp4a.40.2", bitrate=320)

    fallback_result = SimpleNamespace(
        quality=AudioQuality(
            codec="flac",
            lossless=True,
            bit_depth=16,
            sample_rate_hz=44100,
        )
    )
    fallback = SimpleNamespace(
        get_downloadable=AsyncMock(return_value=fallback_result)
    )
    client._api_request = request
    client._get_lossless_fallback_client = AsyncMock(return_value=fallback)

    result = await client.get_downloadable("123", quality=3)

    assert result is fallback_result
    fallback.get_downloadable.assert_awaited_once_with("123", quality=2)
