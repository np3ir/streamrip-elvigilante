import asyncio
import json
import os
from unittest.mock import AsyncMock, Mock, patch

import pytest

from streamrip.config import Config
from streamrip.exceptions import TidalRateLimitError
from streamrip.file_publish import PublishError
from streamrip.library import (
    LibraryCheckpoint,
    LibraryManifest,
    PendingLibraryTrack,
    bounded_ordered_map,
    iter_library_tracks,
    library_job_signature,
)
from streamrip.media.track import Track
from streamrip.metadata.util import DEFAULT_ARTIST_SEPARATOR
from streamrip.rip.main import Main


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
    assert result[0].reference_metadata["id"] == "t1"


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


@pytest.mark.asyncio
async def test_pending_library_track_combines_reference_metadata_with_winner_audio(
    tmp_path,
):
    config = Config("tests/test_config.toml")
    config.session.downloads.folder = str(tmp_path)
    config.session.downloads.source_subdirectories = True
    config.session.downloads.disc_subdirectories = False
    reference = Mock(source="tidal", session=Mock())
    reference.get_metadata = AsyncMock(return_value={"id": "t1"})
    winner = Mock(source="deezer")
    downloadable = Mock(source="deezer", extension="flac")
    winner.get_downloadable = AsyncMock(return_value=downloadable)
    album = Mock(disctotal=1, covers=Mock())
    album.format_folder_path.return_value = "Canonical Artist/Canonical Album"
    metadata = Mock(discnumber=1)
    pending = PendingLibraryTrack(
        "t1", reference, "d1", winner, 2, config, Mock()
    )

    with (
        patch.object(
            PendingLibraryTrack,
            "_canonical_album",
            new=AsyncMock(return_value=album),
        ),
        patch(
            "streamrip.library.TrackMetadata.from_resp",
            return_value=metadata,
        ) as build_metadata,
        patch(
            "streamrip.library.download_artwork",
            new=AsyncMock(return_value=("cover.jpg", None)),
        ),
        patch(
                "streamrip.library.fetch_lrc_from_sources",
            new=AsyncMock(return_value="lyrics"),
        ),
    ):
        resolved = await pending.resolve()

    assert resolved.meta is metadata
    assert resolved.downloadable is downloadable
    assert resolved.cover_path == "cover.jpg"
    assert resolved.lrc_content == "lyrics"
    assert resolved.folder == str(
        tmp_path / "Tidal" / "Canonical Artist" / "Canonical Album"
    )
    assert resolved.folder == os.path.normpath(resolved.folder)
    build_metadata.assert_called_once_with(
        album, "tidal", {"id": "t1"}, DEFAULT_ARTIST_SEPARATOR
    )
    winner.get_downloadable.assert_awaited_once_with("d1", 2)


@pytest.mark.asyncio
async def test_pending_library_track_reuses_metadata_after_tidal_breaker(tmp_path):
    config = Config("tests/test_config.toml")
    config.session.downloads.folder = str(tmp_path)
    config.session.downloads.source_subdirectories = False
    config.session.downloads.disc_subdirectories = False
    cached = {"id": "t1", "title": "Cached"}
    reference = Mock(source="tidal", session=Mock())
    reference.get_metadata = AsyncMock(
        side_effect=TidalRateLimitError("breaker tripped")
    )
    winner = Mock(source="deezer")
    winner.get_downloadable = AsyncMock(return_value=Mock(extension="flac"))
    album = Mock(disctotal=1, covers=Mock())
    album.format_folder_path.return_value = "Artist/Album"
    metadata = Mock(discnumber=1)
    pending = PendingLibraryTrack(
        "t1", reference, "d1", winner, 2, config, Mock(),
        reference_metadata=cached,
    )

    with (
        patch.object(
            PendingLibraryTrack,
            "_canonical_album",
            new=AsyncMock(return_value=album),
        ),
        patch(
            "streamrip.library.TrackMetadata.from_resp",
            return_value=metadata,
        ) as build_metadata,
        patch(
            "streamrip.library.download_artwork",
            new=AsyncMock(return_value=(None, None)),
        ),
        patch(
            "streamrip.library.fetch_lrc_from_sources",
            new=AsyncMock(return_value=None),
        ),
    ):
        resolved = await pending.resolve()

    assert resolved.meta is metadata
    build_metadata.assert_called_once_with(
        album, "tidal", cached, DEFAULT_ARTIST_SEPARATOR
    )
    reference.get_metadata.assert_awaited_once_with("t1", "track")


@pytest.mark.asyncio
async def test_track_completion_callback_runs_for_verified_existing_file(tmp_path):
    config = Config("tests/test_config.toml")
    metadata = Mock(title="Canonical", artist="Artist", isrc="ISRC1")
    metadata.info = Mock(id="t1", explicit=False)
    metadata.format_track_path.return_value = "Canonical"
    downloadable = Mock(source="deezer", extension="flac")
    database = Mock()
    database.downloaded.return_value = False
    database.isrc_downloaded.return_value = False
    completed = Mock()
    (tmp_path / "Canonical.flac").write_bytes(b"existing")
    item = Track(
        metadata,
        downloadable,
        config,
        str(tmp_path),
        None,
        database,
        completion_callback=completed,
    )

    await item.rip()

    completed.assert_called_once_with(str(tmp_path / "Canonical.flac"))


@pytest.mark.asyncio
async def test_existing_audio_repairs_missing_lrc_without_redownload(tmp_path):
    config = Config("tests/test_config.toml")
    metadata = Mock(title="Canonical", artist="Artist", isrc="ISRC1")
    metadata.info = Mock(id="t1", explicit=False)
    metadata.format_track_path.return_value = "Canonical"
    downloadable = Mock(source="tidal", extension="flac")
    downloadable.download = AsyncMock()
    database = Mock()
    database.downloaded.return_value = True
    database.isrc_downloaded.return_value = True
    audio_path = tmp_path / "Canonical.flac"
    audio_path.write_bytes(b"existing")
    item = Track(
        metadata,
        downloadable,
        config,
        str(tmp_path),
        None,
        database,
        lrc_content="[00:01.00]Lyrics",
    )

    await item.rip()

    assert (tmp_path / "Canonical.lrc").read_text(encoding="utf-8") == (
        "[00:01.00]Lyrics"
    )
    downloadable.download.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_audio_preserves_nonempty_lrc_without_redownload(tmp_path):
    config = Config("tests/test_config.toml")
    metadata = Mock(title="Canonical", artist="Artist", isrc="ISRC1")
    metadata.info = Mock(id="t1", explicit=False)
    metadata.format_track_path.return_value = "Canonical"
    downloadable = Mock(source="tidal", extension="flac")
    downloadable.download = AsyncMock()
    database = Mock()
    database.downloaded.return_value = True
    database.isrc_downloaded.return_value = True
    (tmp_path / "Canonical.flac").write_bytes(b"existing")
    lrc_path = tmp_path / "Canonical.lrc"
    lrc_path.write_text("[00:01.00]Keep me", encoding="utf-8")
    item = Track(
        metadata,
        downloadable,
        config,
        str(tmp_path),
        None,
        database,
        lrc_content="[00:02.00]Replacement",
    )

    await item.rip()

    assert lrc_path.read_text(encoding="utf-8") == "[00:01.00]Keep me"
    downloadable.download.assert_not_awaited()


@pytest.mark.asyncio
async def test_track_failure_does_not_advance_resume_checkpoint(tmp_path):
    config = Config("tests/test_config.toml")
    config.session.downloads.max_retries = 0
    metadata = Mock(title="Canonical", artist="Artist", isrc="ISRC1")
    metadata.info = Mock(id="t1", explicit=False)
    metadata.format_track_path.return_value = "Canonical"
    downloadable = Mock(source="deezer", extension="flac")
    downloadable.size = AsyncMock(return_value=100)
    downloadable.download = AsyncMock(side_effect=OSError("network failed"))
    database = Mock()
    database.isrc_downloaded.return_value = False
    completed = Mock()
    failed = Mock()
    item = Track(
        metadata,
        downloadable,
        config,
        str(tmp_path),
        None,
        database,
        completion_callback=completed,
        failure_callback=failed,
        failure_id="d1",
    )

    await item.rip()

    completed.assert_not_called()
    failed.assert_called_once_with(str(tmp_path / "Canonical.flac"))
    database.set_failed.assert_called_once_with("deezer", "track", "d1")


@pytest.mark.asyncio
async def test_publish_failure_is_not_redownloaded_and_keeps_checkpoint_pending(
    tmp_path,
):
    config = Config("tests/test_config.toml")
    config.session.downloads.max_retries = 3
    metadata = Mock(title="Canonical", artist="Artist", isrc="ISRC1")
    metadata.info = Mock(id="t1", explicit=False)
    metadata.format_track_path.return_value = "Canonical"
    retained = tmp_path / "verified-stage.flac"
    downloadable = Mock(source="tidal", extension="flac")
    downloadable.size = AsyncMock(return_value=100)
    downloadable.download = AsyncMock(
        side_effect=PublishError("destination unavailable", retained)
    )
    database = Mock()
    database.isrc_downloaded.return_value = False
    completed = Mock()
    failed = Mock()
    item = Track(
        metadata,
        downloadable,
        config,
        str(tmp_path),
        None,
        database,
        completion_callback=completed,
        failure_callback=failed,
        failure_id="winner1",
    )

    await item.rip()

    downloadable.download.assert_awaited_once()
    completed.assert_not_called()
    failed.assert_called_once_with(str(tmp_path / "Canonical.flac"))
    database.set_failed.assert_called_once_with("tidal", "track", "winner1")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["none", "resolve", "rip"])
async def test_worker_reports_each_library_failure_once(failure_stage):
    main = Main.__new__(Main)
    main.queue = asyncio.Queue()
    main.skipped_items = 0
    failed = Mock()
    pending = Mock(failure_callback=failed)

    if failure_stage == "none":
        pending.resolve = AsyncMock(return_value=None)
    elif failure_stage == "resolve":
        pending.resolve = AsyncMock(side_effect=RuntimeError("metadata failed"))
    else:
        media = Mock()
        media.rip = AsyncMock(side_effect=RuntimeError("postprocess failed"))
        pending.resolve = AsyncMock(return_value=media)

    await main.queue.put(pending)
    worker = asyncio.create_task(main.worker_loop(0))
    await main.queue.join()
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    failed.assert_called_once_with("")
    assert main.skipped_items == 1


@pytest.mark.asyncio
async def test_persistent_workers_consume_before_streaming_producer_finishes():
    main = Main.__new__(Main)
    main.queue = asyncio.Queue()
    main.producer_tasks = []
    main.download_workers = []
    main.skipped_items = 0
    ripped = asyncio.Event()
    media = Mock()
    media.rip = AsyncMock(side_effect=lambda: ripped.set())
    pending = Mock()
    pending.resolve = AsyncMock(return_value=media)

    main.start_download_workers(count=1, queue_size=2)
    await main.queue.put(pending)
    await asyncio.wait_for(ripped.wait(), timeout=1)

    assert main.queue.maxsize == 2
    assert main.download_workers
    await main.finish_download_workers()
    assert main.download_workers == []


@pytest.mark.asyncio
async def test_bounded_ordered_map_preserves_order_and_limit():
    active = 0
    maximum = 0

    async def items():
        for value in range(8):
            yield value

    async def worker(value):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep((8 - value) / 1000)
        active -= 1
        return value

    result = [item async for item in bounded_ordered_map(items(), worker, 3)]

    assert result == list(range(8))
    assert maximum == 3


def test_manifest_is_jsonl_and_excludes_unrequested_fields(tmp_path):
    manifest = LibraryManifest("job", directory=tmp_path)
    manifest.record(
        "completed",
        key="isrc:ONE",
        reference_source="tidal",
        audio_source="deezer",
        path="Music/Track.flac",
    )

    lines = manifest.path.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    assert len(lines) == 1
    assert event["signature"] == "job"
    assert event["status"] == "completed"
    assert event["key"] == "isrc:ONE"
    assert "credentials" not in event
    assert "reference_metadata" not in event


def test_canonical_folder_does_not_truncate_the_relative_hierarchy(tmp_path):
    config = Config("tests/test_config.toml")
    config.session.downloads.folder = str(tmp_path)
    config.session.downloads.source_subdirectories = False
    pending = PendingLibraryTrack(
        "t1", Mock(source="tidal"), "d1", Mock(), 2, config, Mock()
    )
    relative = os.path.join("Artist", "A" * 180, "Album")
    album = Mock()
    album.format_folder_path.return_value = relative

    result = pending._canonical_folder(album, "tidal")

    assert result == os.path.join(str(tmp_path), relative)
    assert result.endswith(os.path.join("A" * 180, "Album"))
