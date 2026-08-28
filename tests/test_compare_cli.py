from click.testing import CliRunner

from streamrip.rip.cli import _is_help_invocation, rip


def test_compare_command_is_registered_with_safe_preview_help():
    command = rip.commands["compare"]
    assert command is not None
    assert "without downloading audio" in command.help
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
