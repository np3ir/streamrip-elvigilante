import asyncio

import pytest

from streamrip.comparison import (
    MultiSourceComparator,
    download_selected,
    format_quality,
    resolve_comparison_collection,
    search_items,
    service_quality_for_ceiling,
)
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

    async def get_candidate(self, source_id, quality):
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
        async def get_candidate(self, source_id, quality):
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
        async def get_candidate(self, source_id, quality):
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
async def test_conflicting_isrc_is_rejected_before_stream_inspection():
    qobuz = FakeClient(
        "qobuz",
        candidate("qobuz", "q1", 24, 192000),
        [search_page("qobuz", "q1", isrc="DIFFERENT")],
    )

    report = await MultiSourceComparator({"qobuz": qobuz}).compare(REFERENCE)

    assert report.candidates == []
    assert report.selected is None


@pytest.mark.asyncio
async def test_duplicate_results_from_isrc_and_metadata_are_inspected_once():
    qobuz = FakeClient(
        "qobuz",
        candidate("qobuz", "q1", 24, 192000),
        [search_page("qobuz", "q1")],
    )
    calls = 0
    original = qobuz.get_candidate

    async def counted_candidate(source_id, quality):
        nonlocal calls
        calls += 1
        return await original(source_id, quality)

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
