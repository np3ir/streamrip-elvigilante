"""Bounded inspection of measurable properties from remote audio headers."""

from __future__ import annotations

import aiohttp

from ..multisource import AudioQuality

_PROBE_BYTES = 64 * 1024


def parse_flac_streaminfo(data: bytes) -> tuple[int, int, int] | None:
    """Return ``(bit depth, sample rate, channels)`` from a FLAC header.

    Both native FLAC and the FLAC marker embedded in common MP4/DASH
    initialization data are accepted.  The caller deliberately supplies only a
    bounded prefix; no audio payload needs to be downloaded for this check.
    """

    marker = data.find(b"fLaC")
    if marker < 0:
        return None
    offset = marker + 4
    while offset + 4 <= len(data):
        header = data[offset : offset + 4]
        block_type = header[0] & 0x7F
        length = int.from_bytes(header[1:4], "big")
        payload_start = offset + 4
        payload_end = payload_start + length
        if payload_end > len(data):
            return None
        if block_type == 0 and length >= 18:
            packed = int.from_bytes(data[payload_start + 10 : payload_start + 18], "big")
            sample_rate = (packed >> 44) & 0xFFFFF
            channels = ((packed >> 41) & 0x7) + 1
            bit_depth = ((packed >> 36) & 0x1F) + 1
            if sample_rate and bit_depth:
                return bit_depth, sample_rate, channels
            return None
        offset = payload_end
    return None


async def probe_flac_quality(
    session: aiohttp.ClientSession,
    urls: tuple[str, ...],
    advertised: AudioQuality,
) -> AudioQuality | None:
    """Inspect a bounded prefix and return measured FLAC properties if found."""

    for url in urls[:2]:
        async with session.get(
            url,
            headers={"Range": f"bytes=0-{_PROBE_BYTES - 1}"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            response.raise_for_status()
            prefix = await response.content.read(_PROBE_BYTES)
        measured = parse_flac_streaminfo(prefix)
        if measured is not None:
            bit_depth, sample_rate, channels = measured
            return AudioQuality(
                codec=advertised.codec,
                lossless=True,
                bit_depth=bit_depth,
                sample_rate_hz=sample_rate,
                bitrate_kbps=advertised.bitrate_kbps,
                channels=channels,
                spatial=advertised.spatial,
            )
    return None
