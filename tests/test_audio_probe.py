from unittest.mock import Mock

import pytest

from streamrip.client.audio_probe import parse_flac_streaminfo
from streamrip.client.downloadable import TidalDownloadable
from streamrip.exceptions import NonStreamableError


def flac_header(bit_depth: int, sample_rate: int = 44100, channels: int = 2) -> bytes:
    packed = (
        (sample_rate << 44)
        | ((channels - 1) << 41)
        | ((bit_depth - 1) << 36)
        | 1234
    )
    streaminfo = b"\x00" * 10 + packed.to_bytes(8, "big") + b"\x00" * 16
    return b"fLaC" + bytes((0x80, 0, 0, len(streaminfo))) + streaminfo


def test_parse_flac_streaminfo_reads_physical_properties():
    assert parse_flac_streaminfo(flac_header(24, 96000)) == (24, 96000, 2)


def test_parse_flac_streaminfo_ignores_unrecognized_prefix():
    assert parse_flac_streaminfo(b"not an audio header") is None


def test_tidal_stage_rejects_physical_quality_above_limit(tmp_path):
    path = tmp_path / "audio.flac"
    path.write_bytes(flac_header(24))
    downloadable = TidalDownloadable(
        Mock(),
        "https://media/audio.flac",
        "flac",
        None,
        (),
        max_bit_depth=16,
    )

    with pytest.raises(NonStreamableError, match="24-bit"):
        downloadable._validate_stage(str(path))


def test_tidal_stage_accepts_physical_quality_at_limit(tmp_path):
    path = tmp_path / "audio.flac"
    path.write_bytes(flac_header(16))
    downloadable = TidalDownloadable(
        Mock(),
        "https://media/audio.flac",
        "flac",
        None,
        (),
        max_bit_depth=16,
    )

    downloadable._validate_stage(str(path))
