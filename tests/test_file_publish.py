import os
from pathlib import Path

import pytest

from streamrip.client.downloadable import Downloadable
from streamrip.destination_identity import DestinationIdentityError
from streamrip.file_publish import (
    PublishError,
    RecoveryError,
    list_recoveries,
    publish_verified_file,
    register_recovery,
    remove_recovery,
    retry_recovery,
)


class BytesDownloadable(Downloadable):
    def __init__(self, payload: bytes, *, failure: Exception | None = None):
        self.session = None
        self.url = "https://invalid.test/media"
        self.extension = "flac"
        self.source = "test"
        self.payload = payload
        self.failure = failure

    async def _download(self, path, callback):
        Path(path).write_bytes(self.payload)
        callback(len(self.payload))
        if self.failure is not None:
            raise self.failure


@pytest.mark.asyncio
async def test_downloadable_stages_then_atomically_replaces_final(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    staging.mkdir()
    destination = tmp_path / "library" / "track.flac"
    destination.parent.mkdir()
    destination.write_bytes(b"old-valid-file")
    monkeypatch.setattr("tempfile.tempdir", os.fspath(staging))

    downloadable = BytesDownloadable(b"new-valid-file")
    await downloadable.download(os.fspath(destination), lambda _size: None)

    assert destination.read_bytes() == b"new-valid-file"
    assert list(staging.iterdir()) == []


@pytest.mark.asyncio
async def test_download_failure_preserves_prior_final_and_cleans_invalid_stage(
    tmp_path, monkeypatch
):
    staging = tmp_path / "staging"
    staging.mkdir()
    destination = tmp_path / "library" / "track.flac"
    destination.parent.mkdir()
    destination.write_bytes(b"old-valid-file")
    monkeypatch.setattr("tempfile.tempdir", os.fspath(staging))

    downloadable = BytesDownloadable(b"partial", failure=OSError("network"))
    with pytest.raises(OSError, match="network"):
        await downloadable.download(os.fspath(destination), lambda _size: None)

    assert destination.read_bytes() == b"old-valid-file"
    assert list(staging.iterdir()) == []


@pytest.mark.asyncio
async def test_cross_volume_publish_verifies_copy_and_removes_source(
    tmp_path, monkeypatch
):
    source = tmp_path / "stage.flac"
    source.write_bytes(b"verified" * 1024)
    destination = tmp_path / "library" / "track.flac"
    destination.parent.mkdir()
    monkeypatch.setattr("streamrip.file_publish._same_volume", lambda *_args: False)

    await publish_verified_file(source, destination)

    assert destination.read_bytes() == b"verified" * 1024
    assert not source.exists()


@pytest.mark.asyncio
async def test_cross_volume_publish_accepts_identical_concurrent_destination(
    tmp_path, monkeypatch
):
    payload = b"shared-album-artwork" * 1024
    source = tmp_path / "stage.jpg"
    source.write_bytes(payload)
    destination = tmp_path / "library" / "cover.jpg"
    destination.parent.mkdir()
    destination.write_bytes(payload)
    monkeypatch.setattr("streamrip.file_publish._same_volume", lambda *_args: False)
    monkeypatch.setattr(
        "streamrip.file_publish.os.replace",
        lambda *_args: (_ for _ in ()).throw(PermissionError("in use")),
    )

    await publish_verified_file(source, destination)

    assert destination.read_bytes() == payload
    assert not source.exists()
    assert not list(destination.parent.glob("*.streamrip-part-*"))


@pytest.mark.asyncio
async def test_corrupt_cross_volume_copy_preserves_source_and_prior_final(
    tmp_path, monkeypatch
):
    source = tmp_path / "stage.flac"
    source.write_bytes(b"verified-source")
    destination = tmp_path / "library" / "track.flac"
    destination.parent.mkdir()
    destination.write_bytes(b"old-valid-file")
    monkeypatch.setattr("streamrip.file_publish._same_volume", lambda *_args: False)

    def corrupt_copy(_source, target):
        Path(target).write_bytes(b"corrupt")

    monkeypatch.setattr("streamrip.file_publish.shutil.copy2", corrupt_copy)

    with pytest.raises(PublishError) as error:
        await publish_verified_file(source, destination)

    assert error.value.retained_path == source
    assert source.read_bytes() == b"verified-source"
    assert destination.read_bytes() == b"old-valid-file"
    assert not list(destination.parent.glob("*.streamrip-part-*"))


def test_recovery_registry_round_trip_and_verified_discard(tmp_path):
    recovery = tmp_path / "recovery"
    source = tmp_path / "stage.flac"
    source.write_bytes(b"verified-source")
    destination = tmp_path / "library" / "track.flac"

    entry = register_recovery(source, destination, directory=recovery)

    assert list_recoveries(directory=recovery) == [entry]
    remove_recovery(entry.id, delete_staging=True, directory=recovery)
    assert not source.exists()
    assert list_recoveries(directory=recovery) == []


@pytest.mark.asyncio
async def test_retry_recovery_validates_and_publishes_without_redownload(tmp_path):
    recovery = tmp_path / "recovery"
    source = tmp_path / "stage.flac"
    source.write_bytes(b"verified-source")
    destination = tmp_path / "library" / "track.flac"
    destination.parent.mkdir()
    destination.write_bytes(b"old-valid-file")
    entry = register_recovery(source, destination, directory=recovery)

    await retry_recovery(entry.id, directory=recovery)

    assert destination.read_bytes() == b"verified-source"
    assert not source.exists()
    assert list_recoveries(directory=recovery) == []


@pytest.mark.asyncio
async def test_retry_recovery_rejects_modified_stage_and_preserves_record(tmp_path):
    recovery = tmp_path / "recovery"
    source = tmp_path / "stage.flac"
    source.write_bytes(b"verified-source")
    destination = tmp_path / "library" / "track.flac"
    destination.parent.mkdir()
    entry = register_recovery(source, destination, directory=recovery)
    source.write_bytes(b"tampered-source")

    with pytest.raises(RecoveryError, match="no longer matches"):
        await retry_recovery(entry.id, directory=recovery)

    assert not destination.exists()
    assert list_recoveries(directory=recovery) == [entry]


@pytest.mark.asyncio
async def test_strict_retry_refuses_replaced_destination_volume(tmp_path, monkeypatch):
    from streamrip.destination_identity import marker_path, trust_destination

    recovery = tmp_path / "recovery"
    anchors = tmp_path / "anchors"
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setattr(
        "streamrip.destination_identity.state_directory", lambda: anchors
    )
    anchor = trust_destination(root, directory=anchors)
    source = tmp_path / "stage.flac"
    source.write_bytes(b"verified-source")
    destination = root / "track.flac"
    entry = register_recovery(
        source,
        destination,
        destination_root=root,
        destination_anchor_id=anchor.anchor_id,
        directory=recovery,
    )
    marker_path(root).write_text(
        '{"format":"streamrip-destination-anchor","version":1,'
        '"anchor_id":"different"}',
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="does not match"):
        await retry_recovery(entry.id, identity_mode="strict", directory=recovery)

    assert source.read_bytes() == b"verified-source"
    assert not destination.exists()
    assert list_recoveries(directory=recovery) == [entry]


@pytest.mark.asyncio
async def test_downloadable_registers_publish_failure(tmp_path, monkeypatch):
    recovery = tmp_path / "recovery"
    staging = tmp_path / "staging"
    staging.mkdir()
    destination = tmp_path / "missing" / "track.flac"
    monkeypatch.setattr("tempfile.tempdir", os.fspath(staging))
    monkeypatch.setattr(
        "streamrip.file_publish.default_recovery_directory", lambda: recovery
    )

    with pytest.raises(PublishError) as error:
        await BytesDownloadable(b"verified-source").download(
            os.fspath(destination), lambda _size: None
        )

    entries = list_recoveries(directory=recovery)
    assert len(entries) == 1
    assert error.value.recovery_id == entries[0].id
    assert f"recovery ID {entries[0].id}" in str(error.value)
    assert Path(entries[0].staging_path).read_bytes() == b"verified-source"
    assert entries[0].destination_path == os.fspath(destination.resolve())


@pytest.mark.asyncio
async def test_registry_failure_does_not_hide_retained_stage(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    staging.mkdir()
    destination = tmp_path / "missing" / "track.flac"
    monkeypatch.setattr("tempfile.tempdir", os.fspath(staging))
    monkeypatch.setattr(
        "streamrip.client.downloadable.register_recovery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("registry unavailable")
        ),
    )

    with pytest.raises(PublishError) as error:
        await BytesDownloadable(b"verified-source").download(
            os.fspath(destination), lambda _size: None
        )

    assert error.value.retained_path.read_bytes() == b"verified-source"
    assert "recovery registration failed: registry unavailable" in str(error.value)


@pytest.mark.asyncio
async def test_identity_change_after_download_retains_and_registers_stage(
    tmp_path, monkeypatch
):
    recovery = tmp_path / "recovery"
    staging = tmp_path / "staging"
    staging.mkdir()
    destination = tmp_path / "library" / "track.flac"
    destination.parent.mkdir()
    monkeypatch.setattr("tempfile.tempdir", os.fspath(staging))
    monkeypatch.setattr(
        "streamrip.file_publish.default_recovery_directory", lambda: recovery
    )
    monkeypatch.setattr(
        "streamrip.destination_identity.check_destination",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            DestinationIdentityError("destination identity does not match")
        ),
    )

    with pytest.raises(PublishError, match="identity does not match") as error:
        await BytesDownloadable(b"verified-source").download(
            os.fspath(destination),
            lambda _size: None,
            destination_root=os.fspath(destination.parent),
            destination_anchor_id="original",
        )

    entries = list_recoveries(directory=recovery)
    assert error.value.recovery_id == entries[0].id
    assert entries[0].destination_anchor_id == "original"
    assert Path(entries[0].staging_path).read_bytes() == b"verified-source"
    assert not destination.exists()
