"""Inspect and safely normalize downloaded audio containers."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

from .exceptions import ConversionError


def is_mp4_container(path: str | Path) -> bool:
    """Detect an ISO Base Media/MP4 container from its ``ftyp`` box."""

    try:
        with open(path, "rb") as audio:
            header = audio.read(12)
    except OSError:
        return False
    return len(header) >= 8 and header[4:8] == b"ftyp"


async def extract_flac_from_mp4(path: str | Path) -> str:
    """Losslessly extract a FLAC stream from MP4 and publish it atomically."""

    source = Path(path)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ConversionError("FFmpeg is required to extract TIDAL FLAC from MP4")

    destination = source.with_suffix(".flac")
    temporary = destination.with_name(f".{destination.name}.streamrip-extract.tmp.flac")
    try:
        process = await asyncio.to_thread(
            subprocess.run,
            [
                ffmpeg,
                "-y",
                "-i",
                os.fspath(source),
                "-map",
                "0:a:0",
                "-c:a",
                "copy",
                os.fspath(temporary),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if process.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            message = process.stderr.decode(errors="replace").strip()
            raise ConversionError(f"Could not extract TIDAL FLAC from MP4: {message}")
        os.replace(temporary, destination)
        if source != destination:
            source.unlink()
        return os.fspath(destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


async def normalize_tidal_container(path: str, downloadable) -> str:
    """Normalize a TIDAL file using delivered quality, never requested labels."""

    quality = getattr(downloadable, "quality", None)
    if quality is None or not quality.lossless or not is_mp4_container(path):
        return path
    return await extract_flac_from_mp4(path)
