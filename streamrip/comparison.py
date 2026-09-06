"""Concurrent cross-service track comparison."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .multisource import (
    MatchKind,
    QualityCeiling,
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
    ceiling: QualityCeiling | None = None
    service_priority: tuple[str, ...] = ("tidal", "deezer", "qobuz")

    @property
    def selected(self) -> ServiceCandidate | None:
        if not self.candidates:
            return None
        try:
            return choose_best(
                self.candidates, self.ceiling, self.service_priority
            )
        except ValueError:
            return None


@dataclass(slots=True)
class ComparisonCollection:
    """Ordered track references resolved from a collection."""

    name: str
    track_ids: list[str]
    media_type: str
    track_metadata: dict[str, dict] = field(default_factory=dict)


def _track_ids(source: str, response: dict) -> list[str]:
    """Extract ordered track IDs from an album or playlist response."""

    tracks = response.get("tracks") or []
    if isinstance(tracks, dict):
        tracks = tracks.get("items") or tracks.get("data") or []
    return [str(item["id"]) for item in tracks if item.get("id") is not None]


def _track_metadata(response: dict) -> dict[str, dict]:
    tracks = response.get("tracks") or []
    if isinstance(tracks, dict):
        tracks = tracks.get("items") or tracks.get("data") or []
    return {
        str(item["id"]): item
        for item in tracks
        if isinstance(item, dict) and item.get("id") is not None
    }


def _collection_name(response: dict, fallback: str) -> str:
    return str(response.get("title") or response.get("name") or fallback)


async def resolve_comparison_collection(
    client,
    media_type: str,
    item_id: str,
) -> ComparisonCollection:
    """Resolve a track, album, playlist, or artist without filesystem effects."""

    if media_type == "track":
        return ComparisonCollection(f"Track {item_id}", [str(item_id)], media_type)

    if media_type in {"album", "playlist", "mix"}:
        response = await client.get_metadata(item_id, media_type)
        return ComparisonCollection(
            _collection_name(response, f"{media_type.title()} {item_id}"),
            _track_ids(client.source, response),
            media_type,
            _track_metadata(response),
        )

    if media_type != "artist":
        raise ValueError(f"Unsupported comparison type: {media_type}")

    from .metadata import ArtistMetadata

    response = await client.get_metadata(item_id, "artist")
    artist = ArtistMetadata.from_resp(response, client.source)
    album_ids: list[str] = [str(album_id) for album_id in artist.album_ids()]
    album_responses: list[dict] = []
    for start in range(0, len(album_ids), 8):
        batch = await asyncio.gather(
            *(
                client.get_metadata(album_id, "album")
                for album_id in album_ids[start : start + 8]
            ),
            return_exceptions=True,
        )
        album_responses.extend(
            response for response in batch if isinstance(response, dict)
        )
    ordered: list[str] = []
    metadata: dict[str, dict] = {}
    seen: set[str] = set()
    for album in album_responses:
        metadata.update(_track_metadata(album))
        for track_id in _track_ids(client.source, album):
            if track_id not in seen:
                seen.add(track_id)
                ordered.append(track_id)
    return ComparisonCollection(artist.name, ordered, media_type, metadata)


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


def service_quality_for_ceiling(
    source: str,
    configured_quality: int,
    ceiling: QualityCeiling | None,
) -> int:
    """Avoid requesting a service tier known to exceed a bit-depth ceiling."""

    if ceiling is None or ceiling.bit_depth is None:
        return configured_quality
    if ceiling.bit_depth < 16:
        return min(configured_quality, 1)
    if ceiling.bit_depth == 16 and source in {"tidal", "qobuz", "deezer"}:
        return min(configured_quality, 2)
    return configured_quality


class MultiSourceComparator:
    """Find and inspect equivalent recordings across authenticated clients."""

    def __init__(
        self,
        clients: dict[str, object],
        *,
        search_limit: int = 10,
        service_priority: tuple[str, ...] = ("tidal", "deezer", "qobuz"),
        source_timeout: float = 45.0,
    ):
        self.clients = clients
        self.search_limit = search_limit
        self.service_priority = service_priority
        self.source_timeout = source_timeout

    async def compare(
        self,
        reference: TrackIdentity,
        quality_by_source: dict[str, int] | None = None,
        reference_candidate: ServiceCandidate | None = None,
        ceiling: QualityCeiling | None = None,
    ) -> ComparisonReport:
        report = ComparisonReport(
            reference,
            ceiling=ceiling,
            service_priority=self.service_priority,
        )
        qualities = quality_by_source or {}
        results = await asyncio.gather(
            *(
                asyncio.wait_for(
                    self._candidate_for_source(
                        source,
                        client,
                        reference,
                        qualities.get(source, getattr(client, "max_quality", 0)),
                        reference_candidate if source == reference.source else None,
                    ),
                    timeout=self.source_timeout,
                )
                for source, client in self.clients.items()
                if source in {"tidal", "qobuz", "deezer"}
            ),
            return_exceptions=True,
        )

        sources = [
            source for source in self.clients if source in {"tidal", "qobuz", "deezer"}
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
        seed: ServiceCandidate | None = None,
    ) -> ServiceCandidate | None:
        verified: list[ServiceCandidate] = []
        candidate_errors: list[Exception] = []
        seen_ids: set[str] = set()
        if seed is not None:
            verified.append(seed)
            seen_ids.add(seed.identity.source_id)
        elif source == reference.source:
            try:
                candidate = await client.get_candidate(reference.source_id, quality)
            except Exception as error:
                candidate_errors.append(error)
            else:
                if match_tracks(reference, candidate.identity) is not MatchKind.NONE:
                    verified.append(candidate)
                    seen_ids.add(candidate.identity.source_id)

        from .client.candidate import track_identity

        matches: list[tuple[int, TrackIdentity]] = []
        queries = []
        if reference.isrc:
            queries.append(reference.isrc.strip())
        metadata_query = f"{reference.artist} {reference.title}".strip()
        if metadata_query and metadata_query not in queries:
            queries.append(metadata_query)

        for query_index, query in enumerate(queries):
            pages = await client.search("track", query, limit=self.search_limit)
            for position, item in enumerate(search_items(source, pages)):
                identity = track_identity(source, item)
                if not identity.source_id or identity.source_id in seen_ids:
                    continue
                seen_ids.add(identity.source_id)
                kind = match_tracks(reference, identity)
                if kind is not MatchKind.NONE:
                    match_priority = 0 if kind is MatchKind.ISRC else 1
                    priority = (
                        match_priority * len(queries) * self.search_limit
                        + query_index * self.search_limit
                        + position
                    )
                    matches.append((priority, identity))

        for _, identity in sorted(matches, key=lambda pair: pair[0]):
            try:
                candidate = await client.get_candidate(identity.source_id, quality)
            except Exception as error:
                candidate_errors.append(error)
                continue
            if match_tracks(reference, candidate.identity) is not MatchKind.NONE:
                verified.append(candidate)
        if verified:
            return choose_best(verified)
        if candidate_errors:
            raise candidate_errors[-1]
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


async def download_selected(main, report: ComparisonReport) -> ServiceCandidate:
    """Queue and download only the report's highest-fidelity track."""

    selected = report.selected
    if selected is None:
        raise ValueError("No playable candidate is available to download")
    await main.add_by_id(
        selected.identity.source,
        "track",
        selected.identity.source_id,
    )
    await main.rip()
    return selected
