from unittest.mock import AsyncMock, Mock, patch

import pytest

from streamrip.config import Config
from streamrip.db import Dummy
from streamrip.exceptions import MissingCredentialsError
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


@pytest.mark.asyncio
async def test_noninteractive_login_does_not_prompt_for_missing_credentials():
    main = object.__new__(Main)
    client = Mock(logged_in=False)
    main.clients = {"deezer": client}
    main.config = Mock()
    prompter = Mock()
    prompter.has_creds.return_value = False
    prompter.prompt_and_login = AsyncMock()

    with patch("streamrip.rip.main.get_prompter", return_value=prompter):
        with pytest.raises(MissingCredentialsError, match="deezer"):
            await main.get_logged_in_client("deezer", prompt_on_missing=False)

    prompter.prompt_and_login.assert_not_awaited()
