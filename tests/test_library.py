import json

import pytest

from streamrip.library import (
    LibraryCheckpoint,
    iter_library_tracks,
    library_job_signature,
)


def track(track_id, isrc, album_id="a1", artists=None):
    return {
        "id": track_id,
        "title": f"Track {track_id}",
        "isrc": isrc,
        "duration": 180,
        "artist": {"id": "ar1", "name": "Artist"},
        "artists": artists or [{"id": "ar1", "name": "Artist"}],
        "album": {"id": album_id, "title": f"Album {album_id}"},
    }


class FakeClient:
    source = "tidal"

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def get_metadata(self, item_id, media_type):
        self.calls.append((str(item_id), media_type))
        return self.responses[(str(item_id), media_type)]


async def collect(client, media_type, item_id, expansion):
    return [
        item
        async for item in iter_library_tracks(
            client, media_type, item_id, expansion
        )
    ]


@pytest.mark.asyncio
async def test_playlist_tracks_preserve_order_and_recording_keys():
    client = FakeClient(
        {("p1", "playlist"): {"tracks": [track("t1", "ISRC1"), track("t2", "ISRC2")]}}
    )

    result = await collect(client, "playlist", "p1", "tracks")

    assert [item.source_id for item in result] == ["t1", "t2"]
    assert [item.recording_key for item in result] == ["isrc:ISRC1", "isrc:ISRC2"]


@pytest.mark.asyncio
async def test_playlist_album_expansion_fetches_each_album_once():
    client = FakeClient(
        {
            ("p1", "playlist"): {
                "tracks": [track("t1", "I1", "a1"), track("t2", "I2", "a1")]
            },
            ("a1", "album"): {"tracks": [track("x1", "X1", "a1")]},
        }
    )

    result = await collect(client, "playlist", "p1", "albums")

    assert [item.source_id for item in result] == ["x1"]
    assert client.calls.count(("a1", "album")) == 1


@pytest.mark.asyncio
async def test_playlist_artist_expansion_uses_every_credited_artist():
    credited = [{"id": "ar1", "name": "One"}, {"id": "ar2", "name": "Two"}]
    client = FakeClient(
        {
            ("p1", "playlist"): {"tracks": [track("t1", "I1", artists=credited)]},
            ("ar1", "artist"): {"name": "One", "albums": [{"id": "a1"}]},
            ("ar2", "artist"): {"name": "Two", "albums": [{"id": "a2"}]},
            ("a1", "album"): {"tracks": [track("x1", "X1", "a1")]},
            ("a2", "album"): {"tracks": [track("x2", "X2", "a2")]},
        }
    )

    result = await collect(client, "playlist", "p1", "artists")

    assert [item.source_id for item in result] == ["x1", "x2"]


@pytest.mark.asyncio
async def test_artist_expansion_does_not_refetch_shared_album():
    credited = [{"id": "ar1", "name": "One"}, {"id": "ar2", "name": "Two"}]
    client = FakeClient(
        {
            ("p1", "playlist"): {"tracks": [track("t1", "I1", artists=credited)]},
            ("ar1", "artist"): {"name": "One", "albums": [{"id": "shared"}]},
            ("ar2", "artist"): {"name": "Two", "albums": [{"id": "shared"}]},
            ("shared", "album"): {"tracks": [track("x1", "X1", "shared")]},
        }
    )

    result = await collect(client, "playlist", "p1", "artists")

    assert [item.source_id for item in result] == ["x1"]
    assert client.calls.count(("shared", "album")) == 1


def test_checkpoint_persists_atomically_and_tolerates_corruption(tmp_path):
    checkpoint = LibraryCheckpoint("job", tmp_path).load()
    checkpoint.mark_done("isrc:ONE")

    assert LibraryCheckpoint("job", tmp_path).load().is_done("isrc:ONE")
    assert not list(tmp_path.glob("*.tmp"))

    (tmp_path / "job.json").write_text("broken", encoding="utf-8")
    assert LibraryCheckpoint("job", tmp_path).load().completed == set()


def test_job_signature_changes_with_quality_and_mode():
    base = {"url": "playlist/1", "expansion": "tracks", "bit_depth": 16}

    assert library_job_signature(base) == library_job_signature(dict(base))
    assert library_job_signature(base) != library_job_signature(
        {**base, "expansion": "albums"}
    )
    assert library_job_signature(base) != library_job_signature(
        {**base, "bit_depth": 24}
    )


def test_checkpoint_file_contains_no_track_metadata(tmp_path):
    checkpoint = LibraryCheckpoint("job", tmp_path)
    checkpoint.mark_done("isrc:ONE")
    data = json.loads((tmp_path / "job.json").read_text(encoding="utf-8"))

    assert data == {"signature": "job", "completed": ["isrc:ONE"]}
