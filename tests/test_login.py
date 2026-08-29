from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from streamrip.config import Config
from streamrip.rip.cli import rip
from streamrip.rip.login import (
    authenticate_deezer,
    authenticate_qobuz,
    configured_services,
    logout_service,
)


@pytest.mark.asyncio
async def test_qobuz_password_is_replaced_by_resulting_token():
    config = Config("tests/test_config.toml")
    client = AsyncMock()
    client.user_id = "123"
    client.user_auth_token = "private-token"
    client.session = None

    with patch("streamrip.rip.login.QobuzClient", return_value=client):
        user_id = await authenticate_qobuz(
            config,
            "person@example.com",
            "plain-password",
            use_auth_token=False,
        )

    assert user_id == "123"
    assert config.file.qobuz.use_auth_token is True
    assert config.file.qobuz.email_or_userid == "123"
    assert config.file.qobuz.password_or_token == "private-token"
    assert "plain-password" not in str(config.file.toml)
    client.login.assert_awaited_once()


@pytest.mark.asyncio
async def test_deezer_arl_is_persisted_only_after_successful_validation():
    config = Config("tests/test_config.toml")
    config.file.deezer.arl = "previous-valid-arl"
    client = AsyncMock()
    client.session = None
    client.client.current_user = {"name": "Listener"}

    with patch("streamrip.rip.login.DeezerClient", return_value=client):
        user = await authenticate_deezer(config, "replacement-arl")

    assert user == "Listener"
    assert config.file.deezer.arl == "replacement-arl"
    client.login.assert_awaited_once()


def test_status_reports_presence_without_secret_values():
    config = Config("tests/test_config.toml")
    config.file.qobuz.email_or_userid = "123"
    config.file.qobuz.password_or_token = "secret-qobuz"
    config.file.deezer.arl = "secret-deezer"

    assert configured_services(config) == {
        "qobuz": True,
        "deezer": True,
        "tidal": bool(config.file.tidal.access_token),
    }


def test_logout_removes_user_secret_but_keeps_qobuz_app_metadata():
    config = Config("tests/test_config.toml")
    app_id = config.file.qobuz.app_id
    secrets = list(config.file.qobuz.secrets)
    config.file.qobuz.email_or_userid = "123"
    config.file.qobuz.password_or_token = "private-token"

    logout_service(config, "qobuz")

    assert config.file.qobuz.email_or_userid == ""
    assert config.file.qobuz.password_or_token == ""
    assert config.file.qobuz.app_id == app_id
    assert config.file.qobuz.secrets == secrets


def test_login_commands_are_registered_and_help_is_non_mutating(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    original = open("tests/test_config_old.toml", "rb").read()
    config_path.write_bytes(original)
    monkeypatch.setattr("sys.argv", ["rip", "login", "qobuz", "--help"])

    result = CliRunner().invoke(
        rip,
        ["--config-path", str(config_path), "login", "qobuz", "--help"],
    )

    assert result.exit_code == 0
    assert "store only the resulting auth token" in result.output
    assert config_path.read_bytes() == original
