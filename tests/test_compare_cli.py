from unittest.mock import patch

from click.testing import CliRunner

from streamrip.rip.cli import _is_help_invocation, rip


def test_compare_command_is_registered_with_safe_preview_help():
    command = rip.commands["compare"]
    assert command is not None
    assert "downloading is opt-in" in command.help
    assert "download_best" in [parameter.name for parameter in command.params]
    assert "max_bit_depth" in [parameter.name for parameter in command.params]
    assert "max_sample_rate" in [parameter.name for parameter in command.params]
    assert [parameter.name for parameter in command.params][-2:] == ["source", "track_id"]


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
