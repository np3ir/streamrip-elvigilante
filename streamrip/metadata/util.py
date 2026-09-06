import functools
import unicodedata
from typing import Iterable, Optional, Type, TypeVar

# Default separator used to join multiple artists when no config is available.
# All public dispatchers (from_resp, from_track_resp, from_album_resp) use this
# as their default so there is a single source of truth.
DEFAULT_ARTIST_SEPARATOR: str = " / "


def normalize_artist_name(name: str) -> str:
    """Comparison key for artist names: accent-insensitive, case-insensitive,
    whitespace-collapsed — so "Rosalia" and "ROSALÍA" count as the same artist.
    Sources often credit the same artist with different normalization (e.g.
    Qobuz album credits vs its performers string)."""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.casefold().split())


def dedup_artists(names: Iterable[str], exclude: Iterable[str] = ()) -> list[str]:
    """Drop duplicate artist names (per normalize_artist_name), keeping order
    and the FIRST spelling seen. Names normalizing equal to any in `exclude`
    are dropped too (e.g. featured artists already credited as main)."""
    seen = {normalize_artist_name(n) for n in exclude}
    out: list[str] = []
    for n in names:
        key = normalize_artist_name(n)
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


def get_album_track_ids(source: str, resp) -> list[str]:
    tracklist = resp["tracks"]
    if source == "qobuz":
        tracklist = tracklist["items"]
    return [track["id"] for track in tracklist]


def safe_get(dictionary, *keys, default=None):
    return functools.reduce(
        lambda d, key: d.get(key, default) if isinstance(d, dict) else default,
        keys,
        dictionary,
    )


T = TypeVar("T")


def typed(thing, expected_type: Type[T]) -> T:
    assert isinstance(thing, expected_type)
    return thing


def get_quality_id(
    bit_depth: Optional[int],
    sampling_rate: Optional[int | float],
) -> int:
    """Get the universal quality id from bit depth and sampling rate.

    :param bit_depth:
    :type bit_depth: Optional[int]
    :param sampling_rate: In kHz
    :type sampling_rate: Optional[int]
    """
    # XXX: Should `0` quality be supported?
    if bit_depth is None or sampling_rate is None:  # is lossy
        return 1

    if bit_depth == 16:
        return 2

    if bit_depth == 24:
        if sampling_rate <= 96:
            return 3

        return 4

    raise Exception(f"Invalid {bit_depth = }")
