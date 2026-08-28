from unittest.mock import patch

from streamrip.config import Config
from streamrip.db import Dummy
from streamrip.rip.main import Main


def test_main_uses_supplied_config_instead_of_appdata(tmp_path, monkeypatch):
    appdata = tmp_path / "appdata" / "streamrip"
    appdata.mkdir(parents=True)
    (appdata / "config.toml").write_text(
        '[downloads]\nfolder = "wrong-folder"\n'
        '[filepaths]\nfolder_format = "wrong-format"\ntrack_format = "wrong-track"\n'
        '[database]\ndownloads_path = "wrong.db"\n'
        'failed_downloads_path = "wrong-failed.db"\n'
    )
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    config = Config("tests/test_config.toml")
    expected_folder = str(tmp_path / "downloads")
    downloads_db = str(tmp_path / "state" / "downloads.db")
    failed_db = str(tmp_path / "state" / "failed.db")
    config.session.downloads.folder = expected_folder
    config.session.filepaths.folder_format = "expected-folder-format"
    config.session.filepaths.track_format = "expected-track-format"
    config.session.database.downloads_path = downloads_db
    config.session.database.failed_downloads_path = failed_db

    with (
        patch("streamrip.rip.main.QobuzClient"),
        patch("streamrip.rip.main.TidalClient"),
        patch("streamrip.rip.main.DeezerClient"),
        patch("streamrip.rip.main.SoundcloudClient"),
        patch("streamrip.rip.main.db.Downloads") as downloads,
        patch("streamrip.rip.main.db.Failed") as failed,
        patch("streamrip.rip.main.db.DownloadedISRCs"),
    ):
        Main(config)

    assert config.session.downloads.folder == expected_folder
    assert config.session.filepaths.folder_format == "expected-folder-format"
    assert config.session.filepaths.track_format == "expected-track-format"
    downloads.assert_called_once_with(downloads_db)
    failed.assert_called_once_with(failed_db)


def test_main_honors_disabled_databases(tmp_path):
    config = Config("tests/test_config.toml")
    config.session.downloads.folder = str(tmp_path / "downloads")
    config.session.database.downloads_enabled = False
    config.session.database.failed_downloads_enabled = False
    config.session.database.isrc_enabled = False

    with (
        patch("streamrip.rip.main.QobuzClient"),
        patch("streamrip.rip.main.TidalClient"),
        patch("streamrip.rip.main.DeezerClient"),
        patch("streamrip.rip.main.SoundcloudClient"),
    ):
        main = Main(config)

    assert isinstance(main.database.downloads, Dummy)
    assert isinstance(main.database.failed, Dummy)
    assert isinstance(main.database.isrcs, Dummy)
