import os

from streamrip.filepath_utils import (
    clean_filepath,
    truncate_filepath_to_max,
)


def test_clean_filepath_preserves_hierarchy_and_caps_each_component_by_bytes():
    path = clean_filepath("Artist/" + ("é" * 200) + "/Album")
    parts = path.split(os.sep)

    assert parts[0] == "Artist"
    assert parts[-1] == "Album"
    assert len(parts) == 3
    assert all(len(part.encode("utf-8")) <= 255 for part in parts)


def test_clean_filepath_normalizes_unicode_and_windows_reserved_names():
    path = clean_filepath("A/Ñandú/CON")
    parts = path.split(os.sep)

    assert parts[1] == "Ñandú"
    assert parts[2].upper() != "CON"


def test_default_total_path_limit_does_not_crush_valid_long_hierarchy():
    path = os.path.join("Artist" * 25, "Album" * 25, "Track.flac")

    assert len(path.encode("utf-8")) > 240
    assert truncate_filepath_to_max(path) == path


def test_explicit_total_path_limit_still_preserves_extension():
    path = os.path.join("folder", ("x" * 300) + ".flac")
    result = truncate_filepath_to_max(path, 240)

    assert len(result.encode("utf-8")) <= 240
    assert result.endswith(".flac")
