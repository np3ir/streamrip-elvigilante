"""Concurrent cross-service track comparison."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace

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
        failures = [result for result in batch if isinstance(result, Exception)]
        if failures:
            raise failures[0]
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


def catalog_match(left: TrackIdentity, right: TrackIdentity) -> MatchKind:
    """Match catalog records while tolerating a demonstrably bad provider ISRC.

    ISRC remains authoritative when equal.  When two providers publish
    conflicting ISRCs, accept only the existing strict metadata match: same
    normalized title (including edition/version), same artist, and duration
    within three seconds.
    """

    match = match_tracks(left, right)
    if match is not MatchKind.NONE:
        return match
    if not left.isrc or not right.isrc:
        return MatchKind.NONE
    return match_tracks(replace(left, isrc=None), replace(right, isrc=None))


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


def service_qualities_for_ceiling(
    source: str,
    configured_quality: int,
    ceiling: QualityCeiling | None,
) -> tuple[int, ...]:
    """Return service tiers needed to evaluate an ordered bit-depth policy."""

    if ceiling is None or not ceiling.bit_depth_order:
        return (service_quality_for_ceiling(source, configured_quality, ceiling),)
    tiers = []
    for depth in ceiling.bit_depth_order:
        if depth < 16:
            tier = min(configured_quality, 1)
        elif depth == 16:
            tier = min(configured_quality, 2)
        else:
            tier = configured_quality
        tiers.append(tier)
    return tuple(tiers)


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
        quality_by_source: dict[str, int | tuple[int, ...]] | None = None,
        reference_candidate: ServiceCandidate | None = None,
        ceiling: QualityCeiling | None = None,
    ) -> ComparisonReport:
        report = ComparisonReport(
            reference,
            ceiling=ceiling,
            service_priority=self.service_priority,
        )
        qualities = quality_by_source or {}
        sources = [
            source for source in self.clients if source in {"tidal", "qobuz", "deezer"}
        ]
        requested = {
            source: (
                value
                if isinstance(value := qualities.get(
                    source, getattr(self.clients[source], "max_quality", 0)
                ), tuple)
                else (value,)
            )
            for source in sources
        }
        rounds = max((len(values) for values in requested.values()), default=0)
        seen_candidates = set()
        for round_index in range(rounds):
            active_sources = [
                source for source in sources if round_index < len(requested[source])
            ]
            results = await asyncio.gather(
                *(
                    asyncio.wait_for(
                        self._candidate_for_source(
                            source,
                            self.clients[source],
                            reference,
                            requested[source][round_index],
                            (
                                reference_candidate
                                if round_index == 0 and source == reference.source
                                else None
                            ),
                            allow_quality_fallback=(
                                ceiling is None or ceiling.fallback_to_lossy
                            ),
                        ),
                        timeout=self.source_timeout,
                    )
                    for source in active_sources
                ),
                return_exceptions=True,
            )
            round_candidates = []
            for source, result in zip(active_sources, results):
                if isinstance(result, Exception):
                    report.errors[source] = f"{type(result).__name__}: {result}"
                elif result is not None:
                    report.errors.pop(source, None)
                    key = (result.identity.source, result.identity.source_id, result.quality)
                    if key not in seen_candidates:
                        seen_candidates.add(key)
                        report.candidates.append(result)
                    round_candidates.append(result)

            if ceiling is not None and ceiling.bit_depth_order:
                target_depth = ceiling.bit_depth_order[round_index]
                if any(
                    item.quality.lossless and item.quality.bit_depth == target_depth
                    for item in round_candidates
                ):
                    break
        return report

    async def _candidate_for_source(
        self,
        source: str,
        client,
        reference: TrackIdentity,
        quality: int | tuple[int, ...],
        seed: ServiceCandidate | None = None,
        *,
        allow_quality_fallback: bool = True,
    ) -> ServiceCandidate | None:
        verified: list[ServiceCandidate] = []
        candidate_errors: list[Exception] = []
        seen_ids: set[str] = set()
        requested_qualities = quality if isinstance(quality, tuple) else (quality,)
        if seed is not None:
            verified.append(seed)
            seen_ids.add(seed.identity.source_id)
        elif source == reference.source:
            for requested_quality in requested_qualities:
                try:
                    candidate = await client.get_candidate(
                        reference.source_id,
                        requested_quality,
                        allow_quality_fallback=allow_quality_fallback,
                    )
                except Exception as error:
                    candidate_errors.append(error)
                else:
                    if catalog_match(reference, candidate.identity) is not MatchKind.NONE:
                        verified.append(candidate)
                        seen_ids.add(candidate.identity.source_id)

        from .client.candidate import track_identity

        matches: list[tuple[int, TrackIdentity]] = []
        if reference.isrc:
            exact_lookup = getattr(client, "lookup_isrc", None)
            if exact_lookup is not None:
                try:
                    exact_item = await exact_lookup(reference.isrc.strip())
                except Exception as error:
                    candidate_errors.append(error)
                    exact_item = None
                if exact_item:
                    exact_identity = track_identity(source, exact_item)
                    if (
                        exact_identity.source_id
                        and exact_identity.source_id not in seen_ids
                        and catalog_match(reference, exact_identity) is MatchKind.ISRC
                    ):
                        seen_ids.add(exact_identity.source_id)
                        matches.append((-1, exact_identity))
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
                kind = catalog_match(reference, identity)
                if kind is not MatchKind.NONE:
                    match_priority = 0 if kind is MatchKind.ISRC else 1
                    priority = (
                        match_priority * len(queries) * self.search_limit
                        + query_index * self.search_limit
                        + position
                    )
                    matches.append((priority, identity))

        for _, identity in sorted(matches, key=lambda pair: pair[0]):
            for requested_quality in requested_qualities:
                try:
                    candidate = await client.get_candidate(
                        identity.source_id,
                        requested_quality,
                        allow_quality_fallback=allow_quality_fallback,
                    )
                except Exception as error:
                    candidate_errors.append(error)
                    continue
                if catalog_match(reference, candidate.identity) is not MatchKind.NONE:
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
