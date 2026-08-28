"""Adapt service metadata and resolved streams into comparable candidates."""

from __future__ import annotations

from ..multisource import (
    AudioQuality,
    ServiceCandidate,
    TrackIdentity,
    normalize_sample_rate,
)


def _artist_name(source: str, metadata: dict) -> str:
    if source == "tidal":
        artists = metadata.get("artists") or []
        names = [item.get("name") for item in artists if item.get("name")]
        return " / ".join(names) or (metadata.get("artist") or {}).get("name", "")
    artist = metadata.get("performer") or metadata.get("artist") or {}
    return artist.get("name", "") if isinstance(artist, dict) else str(artist)


def track_identity(source: str, metadata: dict) -> TrackIdentity:
    """Build a common identity from a service's raw track response."""

    return TrackIdentity(
        source=source,
        source_id=str(metadata.get("id") or metadata.get("SNG_ID") or ""),
        title=str(metadata.get("title") or metadata.get("SNG_TITLE") or ""),
        artist=_artist_name(source, metadata),
        duration_seconds=_duration(metadata),
        isrc=metadata.get("isrc") or metadata.get("ISRC"),
    )


def _duration(metadata: dict) -> int | None:
    value = metadata.get("duration") or metadata.get("DURATION")
    try:
        return round(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _metadata_quality(source: str, metadata: dict) -> AudioQuality:
    if source == "qobuz":
        bit_depth = metadata.get("maximum_bit_depth")
        sample_rate = normalize_sample_rate(metadata.get("maximum_sampling_rate"))
        return AudioQuality(
            codec="flac" if bit_depth and sample_rate else "mp3",
            lossless=bool(bit_depth and sample_rate),
            bit_depth=bit_depth,
            sample_rate_hz=sample_rate,
            channels=metadata.get("maximum_channel_count"),
        )
    if source == "deezer":
        return AudioQuality(codec="unknown", lossless=False)

    quality = str(metadata.get("audioQuality") or "").upper()
    lossless = quality in {"LOSSLESS", "HI_RES", "HI_RES_LOSSLESS"}
    return AudioQuality(
        codec="flac" if lossless else "aac",
        lossless=lossless,
        bit_depth=metadata.get("bitDepth"),
        sample_rate_hz=normalize_sample_rate(metadata.get("sampleRate")),
        bitrate_kbps=metadata.get("bitrate"),
        channels=metadata.get("channels"),
        spatial=str(metadata.get("audioMode") or "").upper() not in {"", "STEREO"},
    )


def service_candidate(source: str, metadata: dict, downloadable) -> ServiceCandidate:
    """Prefer resolved-stream properties and fall back to listing metadata."""

    quality = getattr(downloadable, "audio_quality", None) or getattr(
        downloadable, "quality", None
    )
    if not isinstance(quality, AudioQuality):
        quality = _metadata_quality(source, metadata)
    return ServiceCandidate(track_identity(source, metadata), quality)

