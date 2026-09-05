import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from streamrip.destination_identity import (
    DestinationIdentityError,
    check_destination,
    forget_destination,
    guard_configured_write,
    marker_path,
    trust_destination,
)


def _config(root: Path, mode: str = "strict"):
    downloads = SimpleNamespace(
        folder=str(root), playlist_folder="", destination_identity=mode
    )
    return SimpleNamespace(session=SimpleNamespace(downloads=downloads))


def test_trust_requires_explicit_adoption_and_detects_replaced_marker(tmp_path):
    root = tmp_path / "library"
    state = tmp_path / "state"
    root.mkdir()
    original = trust_destination(root, directory=state)

    with pytest.raises(DestinationIdentityError, match="already exists"):
        trust_destination(root, directory=state)

    marker_path(root).write_text(
        json.dumps(
            {
                "format": "streamrip-destination-anchor",
                "version": 1,
                "anchor_id": "replacement",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(DestinationIdentityError, match="does not match"):
        check_destination(root, root / "album" / "track.flac", directory=state)

    adopted = trust_destination(root, adopt_existing=True, directory=state)
    assert adopted.anchor_id == "replacement"
    assert adopted.anchor_id != original.anchor_id


def test_check_refuses_missing_marker_invalid_state_and_escape(tmp_path):
    root = tmp_path / "library"
    state = tmp_path / "state"
    root.mkdir()
    trust_destination(root, directory=state)

    marker_path(root).unlink()
    with pytest.raises(DestinationIdentityError, match="marker is absent"):
        check_destination(root, root / "track.flac", directory=state)

    marker_path(root).write_text("not json", encoding="utf-8")
    with pytest.raises(DestinationIdentityError, match="marker is invalid"):
        check_destination(root, root / "track.flac", directory=state)

    with pytest.raises(DestinationIdentityError, match="outside"):
        check_destination(root, tmp_path / "library-backup" / "track.flac")


def test_forget_only_removes_local_record(tmp_path):
    root = tmp_path / "library"
    state = tmp_path / "state"
    root.mkdir()
    trust_destination(root, directory=state)

    assert forget_destination(root, directory=state)
    assert marker_path(root).is_file()
    assert not forget_destination(root, directory=state)
    with pytest.raises(DestinationIdentityError, match="not trusted"):
        check_destination(root, root, directory=state)


def test_configured_guard_is_off_by_default_and_strict_fails_closed(
    tmp_path, monkeypatch
):
    root = tmp_path / "library"
    state = tmp_path / "state"
    root.mkdir()
    output = root / "album" / "track.flac"
    monkeypatch.setattr(
        "streamrip.destination_identity.state_directory", lambda: state
    )

    assert guard_configured_write(_config(root, "off"), output) is None
    with pytest.raises(DestinationIdentityError, match="not trusted"):
        guard_configured_write(_config(root), output)

    trusted = trust_destination(root, directory=state)
    assert guard_configured_write(_config(root), output) == trusted
