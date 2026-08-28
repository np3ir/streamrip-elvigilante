"""Concurrent cross-service track comparison."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .multisource import (
    MatchKind,
    ServiceCandidate,
    TrackIdentity,
    choose_best,
    match_tracks,
)


@dataclass(slots=True)
class ComparisonReport:
    reference: TrackIdentity
    candidates: list[ServiceCandidate] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def selected(self) -> ServiceCandidate | None:
        return choose_best(self.candidates) if self.candidates else None


def search_items(source: str, pages: list[dict]) -> list[dict]:
    """Flatten Streamrip's service-specific paginated search responses."""

    items: list[dict] = []
    for page in pages:
        if source == "qobuz":
            items.extend((page.get("tracks") or {}).get("items") or [])
        elif source == "deezer":
            items.extend(page.get("data") or [])
        elif source == "tidal":
            items.extend(page.get("items") or [])
    return items


class MultiSourceComparator:
    """Find and inspect equivalent recordings across authenticated clients."""

    def __init__(self, clients: dict[str, object], *, search_limit: int = 10):
        self.clients = clients
        self.search_limit = search_limit

    async def compare(
        self,
        reference: TrackIdentity,
        quality_by_source: dict[str, int] | None = None,
        reference_candidate: ServiceCandidate | None = None,
    ) -> ComparisonReport:
        report = ComparisonReport(reference)
        if reference_candidate is not None:
            report.candidates.append(reference_candidate)
        qualities = quality_by_source or {}
        results = await asyncio.gather(
            *(
                self._candidate_for_source(
                    source,
                    client,
                    reference,
                    qualities.get(source, getattr(client, "max_quality", 0)),
                )
                for source, client in self.clients.items()
                if source in {"tidal", "qobuz", "deezer"}
                and not (
                    reference_candidate is not None and source == reference.source
                )
            ),
            return_exceptions=True,
        )

        sources = [
            source
            for source in self.clients
            if source in {"tidal", "qobuz", "deezer"}
            and not (
                reference_candidate is not None and source == reference.source
            )
        ]
        for source, result in zip(sources, results):
            if isinstance(result, Exception):
                report.errors[source] = f"{type(result).__name__}: {result}"
            elif result is not None:
                report.candidates.append(result)
        return report

    async def _candidate_for_source(
        self,
        source: str,
        client,
        reference: TrackIdentity,
        quality: int,
    ) -> ServiceCandidate | None:
        if source == reference.source:
            candidate = await client.get_candidate(reference.source_id, quality)
            return candidate if match_tracks(reference, candidate.identity) is not MatchKind.NONE else None

        query = f"{reference.artist} {reference.title}".strip()
        pages = await client.search("track", query, limit=self.search_limit)
        from .client.candidate import track_identity

        matches: list[tuple[int, TrackIdentity]] = []
        for position, item in enumerate(search_items(source, pages)):
            identity = track_identity(source, item)
            kind = match_tracks(reference, identity)
            if kind is not MatchKind.NONE:
                priority = 0 if kind is MatchKind.ISRC else 1
                matches.append((priority * self.search_limit + position, identity))

        for _, identity in sorted(matches, key=lambda pair: pair[0]):
            candidate = await client.get_candidate(identity.source_id, quality)
            if match_tracks(reference, candidate.identity) is not MatchKind.NONE:
                return candidate
        return None


def format_quality(quality) -> str:
    """Human-readable normalized audio properties for CLI reports."""

    parts = [quality.codec.upper()]
    if quality.lossless:
        parts.append("lossless")
    if quality.bit_depth:
        parts.append(f"{quality.bit_depth}-bit")
    if quality.sample_rate_hz:
        khz = quality.sample_rate_hz / 1000
        parts.append(f"{khz:g} kHz")
    if quality.bitrate_kbps:
        parts.append(f"{quality.bitrate_kbps} kbps")
    if quality.channels:
        parts.append(f"{quality.channels} ch")
    if quality.spatial:
        parts.append("spatial")
    return " / ".join(parts)
