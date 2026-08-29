"""Service-neutral matching and audio-quality selection.

The streaming services use incompatible names for equivalent quality tiers.  This
module deliberately compares normalized, measurable stream properties instead of
marketing labels such as ``MAX`` or ``HiFi``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum


class MatchKind(str, Enum):
    ISRC = "isrc"
    METADATA = "metadata"
    NONE = "none"


# Service order applies only after normalized delivered quality is equal.
SERVICE_PRIORITY = {"tidal": 3, "deezer": 2, "qobuz": 1}


def service_priority_rank(priority: list[str] | tuple[str, ...] | None) -> dict[str, int]:
    """Return a complete ranking, preserving configured order and safe defaults."""

    order = list(priority or ())
    order.extend(service for service in SERVICE_PRIORITY if service not in order)
    size = len(order)
    return {service: size - position for position, service in enumerate(order)}


@dataclass(frozen=True, slots=True)
class TrackIdentity:
    source: str
    source_id: str
    title: str
    artist: str
    duration_seconds: int | None = None
    isrc: str | None = None


@dataclass(frozen=True, slots=True)
class AudioQuality:
    """Properties advertised by, or inspected from, an audio stream.

    ``sample_rate_hz`` is always expressed in Hz.  A zero/``None`` value means
    unknown and must never be interpreted as CD quality.
    """

    codec: str
    lossless: bool
    bit_depth: int | None = None
    sample_rate_hz: int | None = None
    bitrate_kbps: int | None = None
    channels: int | None = None
    spatial: bool = False

    @property
    def rank(self) -> tuple[int, int, int, int, int, int]:
        """Return a deterministic fidelity-first ordering.

        Lossless audio wins over lossy spatial audio, matching tiddl-elvigilante's
        FLAC-over-Atmos policy.  Spatial/channel count is only a tie-breaker after
        losslessness, bit depth, sample rate, and bitrate.
        """

        return (
            int(self.lossless),
            self.bit_depth or 0,
            self.sample_rate_hz or 0,
            self.bitrate_kbps or 0,
            self.channels or 0,
            int(self.spatial),
        )


@dataclass(frozen=True, slots=True)
class ServiceCandidate:
    identity: TrackIdentity
    quality: AudioQuality


@dataclass(frozen=True, slots=True)
class QualityCeiling:
    """Maximum delivered fidelity accepted by cross-service selection."""

    bit_depth: int | None = None
    sample_rate_hz: int | None = None
    prefer_lossless: bool = True
    fallback_to_lossy: bool = True

    def allows(self, quality: AudioQuality) -> bool:
        # PCM bit depth/sample rate do not describe lossy codecs consistently;
        # lossy delivery remains the final fallback below lossless candidates.
        if not quality.lossless:
            return self.fallback_to_lossy
        if self.bit_depth is not None:
            if quality.bit_depth is None or quality.bit_depth > self.bit_depth:
                return False
        if self.sample_rate_hz is not None:
            if (
                quality.sample_rate_hz is None
                or quality.sample_rate_hz > self.sample_rate_hz
            ):
                return False
        return True


def normalize_sample_rate(value: int | float | None) -> int | None:
    """Normalize a service sample rate (kHz or Hz) to integer Hz."""

    if value is None or value <= 0:
        return None
    return round(value * 1000) if value < 1000 else round(value)


def _normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def match_tracks(
    left: TrackIdentity,
    right: TrackIdentity,
    *,
    duration_tolerance: int = 3,
) -> MatchKind:
    """Determine whether two service records represent the same recording."""

    left_isrc = (left.isrc or "").strip().upper()
    right_isrc = (right.isrc or "").strip().upper()
    if left_isrc and right_isrc:
        return MatchKind.ISRC if left_isrc == right_isrc else MatchKind.NONE

    if _normalize_text(left.title) != _normalize_text(right.title):
        return MatchKind.NONE
    if _normalize_text(left.artist) != _normalize_text(right.artist):
        return MatchKind.NONE
    if left.duration_seconds is None or right.duration_seconds is None:
        return MatchKind.NONE
    if abs(left.duration_seconds - right.duration_seconds) > duration_tolerance:
        return MatchKind.NONE
    return MatchKind.METADATA


def choose_best(
    candidates: list[ServiceCandidate],
    ceiling: QualityCeiling | None = None,
    service_priority: list[str] | tuple[str, ...] | None = None,
) -> ServiceCandidate:
    """Choose the highest-fidelity candidate with a stable source tie-break."""

    if not candidates:
        raise ValueError("At least one service candidate is required")
    eligible = (
        [item for item in candidates if ceiling.allows(item.quality)]
        if ceiling is not None
        else candidates
    )
    if not eligible:
        raise ValueError("No candidate satisfies the requested quality ceiling")
    priority_rank = service_priority_rank(service_priority)

    def selection_rank(item: ServiceCandidate):
        quality = item.quality
        if ceiling is None or ceiling.prefer_lossless:
            rank = quality.rank
        else:
            rank = (
                quality.bit_depth or 0,
                quality.sample_rate_hz or 0,
                quality.bitrate_kbps or 0,
                quality.channels or 0,
                int(quality.spatial),
                int(quality.lossless),
            )
        return rank, priority_rank.get(item.identity.source, 0)

    return max(eligible, key=selection_rank)
