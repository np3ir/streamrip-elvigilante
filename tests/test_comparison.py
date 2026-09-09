import asyncio

import pytest

from streamrip.comparison import (
    MultiSourceComparator,
    catalog_match,
    download_selected,
    format_quality,
    resolve_comparison_collection,
    search_items,
    service_qualities_for_ceiling,
    service_quality_for_ceiling,
)
from streamrip.exceptions import TidalRateLimitError
from streamrip.multisource import (
    AudioQuality,
    QualityCeiling,
    ServiceCandidate,
    TrackIdentity,
)

REFERENCE = TrackIdentity(
    source="tidal",
    source_id="t1",
    title="Song",
    artist="Artist",
    duration_seconds=180,
    isrc="USABC1234567",
)


def candidate(source, source_id, bit_depth, sample_rate):
    return ServiceCandidate(
        TrackIdentity(
            source=source,
            source_id=source_id,
            title="Song",
            artist="Artist",
            duration_seconds=180,
            isrc="USABC1234567",
        ),
        AudioQuality(
            codec="flac",
            lossless=True,
            bit_depth=bit_depth,
            sample_rate_hz=sample_rate,
        ),
    )


class FakeClient:
    def __init__(self, source, result, pages=None, error=None, delay=0):
        self.source = source
        self.max_quality = 4
        self.result = result
        self.pages = pages or []
        self.error = error
        self.delay = delay
        self.queries = []

    async def search(self, media_type, query, limit):
        assert media_type == "track"
        assert limit == 10
        self.queries.append(query)
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.pages

    async def get_candidate(
        self, source_id, quality, *, allow_quality_fallback=True
    ):
        assert source_id == self.result.identity.source_id
        assert quality == 4
        await asyncio.sleep(self.delay)
        return self.result


def search_page(source, source_id, isrc="USABC1234567"):
    item = {
        "id": source_id,
        "title": "Song",
        "duration": 180,
        "isrc": isrc,
        "artist": {"name": "Artist"},
        "performer": {"name": "Artist"},
    }
    if source == "qobuz":
        return {"tracks": {"items": [item]}}
    if source == "deezer":
        return {"data": [item]}
    return {"items": [item]}


def test_flattens_service_search_pages():
    assert len(search_items("qobuz", [search_page("qobuz", "q1")])) == 1
    assert len(search_items("deezer", [search_page("deezer", "d1")])) == 1
    assert len(search_items("tidal", [search_page("tidal", "t1")])) == 1


def test_catalog_match_tolerates_conflicting_isrc_only_for_strong_metadata():
    tidal = TrackIdentity(
        "tidal", "t1", "Memo Rex (En Vivo)", "Zoé", 407, "MXUM72503877"
    )
    deezer = TrackIdentity(
        "deezer", "d1", "Memo Rex (En Vivo)", "Zoé", 407, "MXUM72503867"
    )
    wrong_edition = TrackIdentity(
        "deezer", "d2", "Memo Rex", "Zoé", 226, "MXF740600001"
    )

    assert catalog_match(tidal, deezer).name == "METADATA"
    assert catalog_match(tidal, wrong_edition).name == "NONE"


def test_formats_normalized_quality_for_cli():
    text = format_quality(
        AudioQuality(
            codec="flac",
            lossless=True,
            bit_depth=24,
            sample_rate_hz=192000,
            channels=2,
        )
    )
    assert text == "FLAC / lossless / 24-bit / 192 kHz / 2 ch"


def test_16_bit_ceiling_requests_cd_tiers_from_all_services():
    ceiling = QualityCeiling(bit_depth=16)

    assert service_quality_for_ceiling("tidal", 4, ceiling) == 2
    assert service_quality_for_ceiling("qobuz", 4, ceiling) == 2
    assert service_quality_for_ceiling("deezer", 2, ceiling) == 2


def test_ordered_depth_policy_requests_cd_and_hires_tiers():
    ceiling = QualityCeiling(bit_depth=24, bit_depth_order=(16, 24))

    assert service_qualities_for_ceiling("tidal", 4, ceiling) == (2, 4)
    assert service_qualities_for_ceiling("qobuz", 3, ceiling) == (2, 3)
    assert service_qualities_for_ceiling("deezer", 2, ceiling) == (2, 2)


@pytest.mark.asyncio
async def test_ordered_depth_comparison_skips_hires_round_when_cd_exists():
    cd = candidate("tidal", "t1", 16, 44100)
    hires = candidate("tidal", "t1", 24, 96000)

    class TierClient:
        source = "tidal"
        max_quality = 4

        def __init__(self):
            self.calls = []

        async def get_candidate(
            self, source_id, quality, *, allow_quality_fallback=True
        ):
            self.calls.append(quality)
            return {2: cd, 4: hires}[quality]

        async def search(self, media_type, query, limit):
            return []

    client = TierClient()
    ceiling = QualityCeiling(
        bit_depth=24,
        fallback_to_lossy=False,
        bit_depth_order=(16, 24),
    )
    report = await MultiSourceComparator({"tidal": client}).compare(
        REFERENCE,
        quality_by_source={"tidal": (2, 4)},
        ceiling=ceiling,
    )

    assert report.selected == cd
    assert client.calls == [2]


@pytest.mark.asyncio
async def test_ordered_depth_comparison_uses_hires_only_after_cd_miss():
    hires = candidate("tidal", "t1", 24, 96000)

    class TierClient:
        source = "tidal"
        max_quality = 4

        def __init__(self):
            self.calls = []

        async def get_candidate(
            self, source_id, quality, *, allow_quality_fallback=True
        ):
            self.calls.append(quality)
            if quality == 2:
                raise RuntimeError("CD tier unavailable")
            return hires

        async def search(self, media_type, query, limit):
            return []

    client = TierClient()
    ceiling = QualityCeiling(
        bit_depth=24,
        fallback_to_lossy=False,
        bit_depth_order=(16, 24),
    )
    report = await MultiSourceComparator({"tidal": client}).compare(
        REFERENCE,
        quality_by_source={"tidal": (2, 4)},
        ceiling=ceiling,
    )

    assert report.selected == hires
    assert client.calls == [2, 4]


class MetadataClient:
    source = "tidal"

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def get_metadata(self, item_id, media_type):
        self.calls.append((item_id, media_type))
        return self.responses[(item_id, media_type)]


@pytest.mark.asyncio
async def test_resolves_album_tracks_without_downloading():
    client = MetadataClient(
        {
            ("a1", "album"): {
                "title": "Album",
                "tracks": [{"id": 1}, {"id": 2}],
            }
        }
    )

    collection = await resolve_comparison_collection(client, "album", "a1")

    assert collection.name == "Album"
    assert collection.track_ids == ["1", "2"]
    assert collection.track_metadata == {"1": {"id": 1}, "2": {"id": 2}}
    assert client.calls == [("a1", "album")]


@pytest.mark.asyncio
async def test_resolves_playlist_track_container():
    client = MetadataClient(
        {
            ("p1", "playlist"): {
                "title": "Playlist",
                "tracks": {"items": [{"id": "t1"}, {"id": "t2"}]},
            }
        }
    )

    collection = await resolve_comparison_collection(client, "playlist", "p1")

    assert collection.track_ids == ["t1", "t2"]
    assert collection.track_metadata["t2"] == {"id": "t2"}


@pytest.mark.asyncio
async def test_resolves_artist_albums_and_deduplicates_tracks_in_order():
    client = MetadataClient(
        {
            ("ar1", "artist"): {
                "name": "Artist",
                "albums": [{"id": "a1"}, {"id": "a2"}],
            },
            ("a1", "album"): {"tracks": [{"id": "t1"}, {"id": "t2"}]},
            ("a2", "album"): {"tracks": [{"id": "t2"}, {"id": "t3"}]},
        }
    )

    collection = await resolve_comparison_collection(client, "artist", "ar1")

    assert collection.name == "Artist"
    assert collection.track_ids == ["t1", "t2", "t3"]
    assert set(collection.track_metadata) == {"t1", "t2", "t3"}


@pytest.mark.asyncio
async def test_artist_resolution_stops_if_any_album_metadata_is_unavailable():
    failure = TidalRateLimitError("breaker tripped")
    client = MetadataClient(
        {
            ("ar1", "artist"): {
                "name": "Artist",
                "albums": [{"id": "a1"}, {"id": "a2"}],
            },
            ("a1", "album"): {"tracks": [{"id": "t1"}]},
            ("a2", "album"): failure,
        }
    )

    async def get_metadata(item_id, media_type):
        result = client.responses[(item_id, media_type)]
        if isinstance(result, Exception):
            raise result
        return result

    client.get_metadata = get_metadata

    with pytest.raises(TidalRateLimitError, match="breaker tripped"):
        await resolve_comparison_collection(client, "artist", "ar1")


@pytest.mark.asyncio
async def test_compares_concurrently_and_selects_highest_fidelity():
    clients = {
        "tidal": FakeClient("tidal", candidate("tidal", "t1", 24, 96000), delay=0.03),
        "qobuz": FakeClient(
            "qobuz",
            candidate("qobuz", "q1", 24, 192000),
            [search_page("qobuz", "q1")],
            delay=0.03,
        ),
        "deezer": FakeClient(
            "deezer",
            candidate("deezer", "d1", 16, 44100),
            [search_page("deezer", "d1")],
            delay=0.03,
        ),
    }

    report = await MultiSourceComparator(clients).compare(REFERENCE)

    assert {item.identity.source for item in report.candidates} == {
        "tidal",
        "qobuz",
        "deezer",
    }
    assert report.selected.identity.source == "qobuz"
    assert report.errors == {}
    assert clients["qobuz"].queries == ["USABC1234567", "Artist Song"]
    assert clients["deezer"].queries == ["USABC1234567", "Artist Song"]


@pytest.mark.asyncio
async def test_exact_isrc_lookup_precedes_empty_text_search():
    deezer_result = candidate("deezer", "d1", 16, 44100)

    class ExactDeezer(FakeClient):
        async def lookup_isrc(self, isrc):
            assert isrc == "USABC1234567"
            return search_page("deezer", "d1")["data"][0]

    deezer = ExactDeezer("deezer", deezer_result, pages=[])

    report = await MultiSourceComparator({"deezer": deezer}).compare(REFERENCE)

    assert report.candidates == [deezer_result]
    assert report.selected == deezer_result


@pytest.mark.asyncio
async def test_strict_ceiling_disables_service_quality_fallback():
    result = candidate("deezer", "d1", 16, 44100)

    class StrictClient(FakeClient):
        async def get_candidate(
            self, source_id, quality, *, allow_quality_fallback=True
        ):
            assert allow_quality_fallback is False
            return await super().get_candidate(
                source_id,
                quality,
                allow_quality_fallback=allow_quality_fallback,
            )

    deezer = StrictClient(
        "deezer", result, [search_page("deezer", "d1")]
    )
    report = await MultiSourceComparator({"deezer": deezer}).compare(
        REFERENCE,
        ceiling=QualityCeiling(bit_depth=16, fallback_to_lossy=False),
    )

    assert report.selected == result


@pytest.mark.asyncio
async def test_reference_candidate_prevents_duplicate_manifest_request():
    tidal = FakeClient("tidal", candidate("tidal", "t1", 24, 96000))
    seed = tidal.result

    report = await MultiSourceComparator({"tidal": tidal}).compare(
        REFERENCE, reference_candidate=seed
    )

    assert report.candidates == [seed]


@pytest.mark.asyncio
async def test_selects_best_matching_edition_within_reference_service():
    seed = candidate("qobuz", "q1", 24, 44100)
    better = candidate("qobuz", "q2", 24, 88200)

    class EditionsClient(FakeClient):
        async def get_candidate(
            self, source_id, quality, *, allow_quality_fallback=True
        ):
            assert quality == 4
            return {"q1": seed, "q2": better}[source_id]

    qobuz = EditionsClient(
        "qobuz",
        seed,
        [
            {
                "tracks": {
                    "items": [
                        search_page("qobuz", "q1")["tracks"]["items"][0],
                        search_page("qobuz", "q2")["tracks"]["items"][0],
                    ]
                }
            }
        ],
    )

    report = await MultiSourceComparator({"qobuz": qobuz}).compare(
        seed.identity, reference_candidate=seed
    )

    assert report.candidates == [better]
    assert report.selected == better


@pytest.mark.asyncio
async def test_unplayable_matching_edition_does_not_hide_a_later_candidate():
    playable = candidate("qobuz", "q2", 24, 88200)

    class EditionsClient(FakeClient):
        async def get_candidate(
            self, source_id, quality, *, allow_quality_fallback=True
        ):
            assert quality == 4
            if source_id == "q1":
                raise RuntimeError("edition unavailable")
            return playable

    qobuz = EditionsClient(
        "qobuz",
        playable,
        [
            {
                "tracks": {
                    "items": [
                        search_page("qobuz", "q1")["tracks"]["items"][0],
                        search_page("qobuz", "q2")["tracks"]["items"][0],
                    ]
                }
            }
        ],
    )

    report = await MultiSourceComparator({"qobuz": qobuz}).compare(REFERENCE)

    assert report.candidates == [playable]
    assert report.errors == {}


@pytest.mark.asyncio
async def test_one_service_failure_does_not_cancel_other_services():
    clients = {
        "tidal": FakeClient("tidal", candidate("tidal", "t1", 24, 96000)),
        "qobuz": FakeClient(
            "qobuz",
            candidate("qobuz", "q1", 24, 192000),
            error=RuntimeError("login failed"),
        ),
    }

    report = await MultiSourceComparator(clients).compare(REFERENCE)

    assert report.selected.identity.source == "tidal"
    assert "login failed" in report.errors["qobuz"]


@pytest.mark.asyncio
async def test_one_hanging_service_times_out_without_blocking_others():
    good = candidate("qobuz", "q1", 16, 44100)

    class HangingClient(FakeClient):
        async def get_candidate(
            self, _source_id, _quality, *, allow_quality_fallback=True
        ):
            await asyncio.Event().wait()

    clients = {
        "tidal": HangingClient("tidal", candidate("tidal", "t1", 16, 44100)),
        "qobuz": FakeClient("qobuz", good, [search_page("qobuz", "q1")]),
    }
    report = await MultiSourceComparator(
        clients, source_timeout=0.01
    ).compare(REFERENCE)

    assert report.selected is good
    assert report.errors["tidal"].startswith("TimeoutError:")


@pytest.mark.asyncio
async def test_conflicting_isrc_with_strong_metadata_is_inspected():
    qobuz = FakeClient(
        "qobuz",
        candidate("qobuz", "q1", 24, 192000),
        [search_page("qobuz", "q1", isrc="DIFFERENT")],
    )

    report = await MultiSourceComparator({"qobuz": qobuz}).compare(REFERENCE)

    assert report.candidates == [qobuz.result]
    assert report.selected == qobuz.result


@pytest.mark.asyncio
async def test_duplicate_results_from_isrc_and_metadata_are_inspected_once():
    qobuz = FakeClient(
        "qobuz",
        candidate("qobuz", "q1", 24, 192000),
        [search_page("qobuz", "q1")],
    )
    calls = 0
    original = qobuz.get_candidate

    async def counted_candidate(
        source_id, quality, *, allow_quality_fallback=True
    ):
        nonlocal calls
        calls += 1
        return await original(
            source_id,
            quality,
            allow_quality_fallback=allow_quality_fallback,
        )

    qobuz.get_candidate = counted_candidate

    report = await MultiSourceComparator({"qobuz": qobuz}).compare(REFERENCE)

    assert report.selected.identity.source == "qobuz"
    assert calls == 1


@pytest.mark.asyncio
async def test_download_selected_queues_only_the_winner():
    class FakeMain:
        def __init__(self):
            self.queued = []
            self.rip_calls = 0

        async def add_by_id(self, source, media_type, source_id):
            self.queued.append((source, media_type, source_id))

        async def rip(self):
            self.rip_calls += 1

    report = await MultiSourceComparator(
        {
            "tidal": FakeClient("tidal", candidate("tidal", "t1", 24, 96000)),
            "qobuz": FakeClient(
                "qobuz",
                candidate("qobuz", "q1", 24, 192000),
                [search_page("qobuz", "q1")],
            ),
        }
    ).compare(REFERENCE)
    main = FakeMain()

    selected = await download_selected(main, report)

    assert selected.identity.source == "qobuz"
    assert main.queued == [("qobuz", "track", "q1")]
    assert main.rip_calls == 1


@pytest.mark.asyncio
async def test_download_selected_rejects_empty_report():
    report = await MultiSourceComparator({}).compare(REFERENCE)

    with pytest.raises(ValueError, match="No playable candidate"):
        await download_selected(object(), report)
