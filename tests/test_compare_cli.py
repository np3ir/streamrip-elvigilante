import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from streamrip.exceptions import TidalRateLimitError
from streamrip.rip.cli import (
    _compare_with_reference_failover,
    _get_logged_in_client_bounded,
    _is_help_invocation,
    rip,
)


def test_compare_command_is_registered_with_safe_preview_help():
    command = rip.commands["compare"]
    assert command is not None
    assert "downloading is opt-in" in command.help
    assert "download_best" in [parameter.name for parameter in command.params]
    assert "max_bit_depth" in [parameter.name for parameter in command.params]
    assert "max_sample_rate" in [parameter.name for parameter in command.params]
    assert "prefer_lossless" in [parameter.name for parameter in command.params]
    assert "fallback_to_lossy" in [parameter.name for parameter in command.params]
    assert "media_type" in [parameter.name for parameter in command.params]
    assert "service_priority" in [parameter.name for parameter in command.params]
    assert [parameter.name for parameter in command.params][-2:] == [
        "source_or_url",
        "item_id",
    ]


def test_library_command_registers_mass_processing_safety_options():
    command = rip.commands["library"]
    names = [parameter.name for parameter in command.params]

    assert command is not None
    assert "expansion" in names
    assert "dry_run" in names
    assert "resume" in names
    assert "max_tracks" in names
    assert "workers" in names
    assert "manifest" in names
    assert "manifest_path" in names


def test_help_invocation_is_detected_before_config_loading():
    assert _is_help_invocation(["compare", "--help"]) is True
    assert _is_help_invocation(["compare", "tidal", "123"]) is False


def test_compare_help_does_not_migrate_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    original = open("tests/test_config_old.toml", "rb").read()
    config_path.write_bytes(original)
    monkeypatch.setattr(
        "sys.argv",
        ["rip", "--config-path", str(config_path), "compare", "--help"],
    )

    result = CliRunner().invoke(
        rip,
        ["--config-path", str(config_path), "compare", "--help"],
    )

    assert result.exit_code == 0
    assert config_path.read_bytes() == original
    assert not list(tmp_path.glob("config.toml.bak*"))


def test_no_db_disables_all_database_writes(monkeypatch):
    observed = {}

    class FakeMain:
        def __init__(self, config):
            database = config.session.database
            observed.update(
                downloads=database.downloads_enabled,
                failed=database.failed_downloads_enabled,
                isrc=database.isrc_enabled,
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def add_by_id(self, *_args):
            return None

        async def resolve(self):
            return None

        async def rip(self):
            return None

    monkeypatch.setattr("sys.argv", ["rip", "--no-db", "id", "tidal", "track", "1"])
    with patch("streamrip.rip.cli.Main", FakeMain):
        result = CliRunner().invoke(
            rip,
            ["--config-path", "tests/test_config.toml", "--no-db", "id", "tidal", "track", "1"],
        )

    assert result.exit_code == 0
    assert observed == {"downloads": False, "failed": False, "isrc": False}


@pytest.mark.asyncio
async def test_service_login_timeout_cancels_hanging_login():
    class HangingMain:
        async def get_logged_in_client(self, *_args, **_kwargs):
            await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        await _get_logged_in_client_bounded(
            HangingMain(), "deezer", timeout=0.01
        )


@pytest.mark.asyncio
async def test_tidal_rate_limit_uses_catalog_identity_and_continues_comparison():
    metadata = {
        "id": "t1",
        "title": "Song",
        "isrc": "USABC1234567",
        "duration": 180,
        "artists": [{"name": "Artist"}],
    }
    reference_client = SimpleNamespace(
        get_downloadable=AsyncMock(
            side_effect=TidalRateLimitError("breaker tripped")
        )
    )
    report = SimpleNamespace(errors={"tidal": "pre-network breaker rejection"})
    comparator = SimpleNamespace(compare=AsyncMock(return_value=report))

    result = await _compare_with_reference_failover(
        source="tidal",
        track_id="t1",
        metadata=metadata,
        reference_client=reference_client,
        reference_quality=2,
        comparator=comparator,
        qualities={"tidal": 2, "deezer": 2, "qobuz": 2},
        ceiling=object(),
    )

    assert result is report
    identity = comparator.compare.await_args.args[0]
    assert identity.source == "tidal"
    assert identity.isrc == "USABC1234567"
    assert comparator.compare.await_args.kwargs.get("reference_candidate") is None
    assert result.errors["tidal"].startswith("TidalRateLimitError:")
