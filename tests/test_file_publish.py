import os
from pathlib import Path

import pytest

from streamrip.client.downloadable import Downloadable
from streamrip.file_publish import PublishError, publish_verified_file


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
