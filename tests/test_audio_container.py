import asyncio
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from streamrip.audio_container import (
    extract_flac_from_mp4,
    is_mp4_container,
    normalize_tidal_container,
)


def test_mp4_detection_uses_bytes_not_extension(tmp_path):
    disguised = tmp_path / "track.flac"
    disguised.write_bytes(b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00")
    actual_flac = tmp_path / "track.m4a"
    actual_flac.write_bytes(b"fLaC\x00\x00\x00\x22")

    assert is_mp4_container(disguised) is True
    assert is_mp4_container(actual_flac) is False


@pytest.mark.asyncio
async def test_non_lossless_tidal_file_is_not_extracted(tmp_path):
    path = tmp_path / "atmos.m4a"
    path.write_bytes(b"\x00\x00\x00\x18ftypM4A ")
    downloadable = SimpleNamespace(quality=SimpleNamespace(lossless=False))

    assert await normalize_tidal_container(os.fspath(path), downloadable) == os.fspath(path)


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")
async def test_extracts_flac_from_real_mp4_without_reencoding(tmp_path):
    source = tmp_path / "wrapped.m4a"
    process = await asyncio.to_thread(
        subprocess.run,
        [
            shutil.which("ffmpeg"),
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.05",
            "-c:a",
            "flac",
            "-f",
            "mp4",
            os.fspath(source),
        ],
        check=False,
    )
    assert process.returncode == 0

    output = await extract_flac_from_mp4(source)

    assert output.endswith(".flac")
    assert not source.exists()
    assert open(output, "rb").read(4) == b"fLaC"
    assert not list(tmp_path.glob("*.tmp.flac"))
