from streamrip.rip.cli import rip


def test_compare_command_is_registered_with_safe_preview_help():
    command = rip.commands["compare"]
    assert command is not None
    assert "without downloading audio" in command.help
    assert [parameter.name for parameter in command.params][-2:] == ["source", "track_id"]
