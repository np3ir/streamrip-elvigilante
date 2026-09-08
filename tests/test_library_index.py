import shutil
from pathlib import Path

from mutagen.flac import FLAC

from streamrip.library_index import LibraryIndex, quality_at_least
from streamrip.multisource import AudioQuality


def tagged_flac(root: Path, name: str, isrc: str) -> Path:
    path = root / name
    shutil.copy2("tests/silence.flac", path)
    audio = FLAC(path)
    audio["isrc"] = isrc
    audio.save()
    return path


def test_scan_is_incremental_and_reads_physical_flac_quality(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    track = tagged_flac(root, "song.flac", " us abc 1234567 ")
    index = LibraryIndex(tmp_path / "index.db")

    first = index.scan(root)
    second = index.scan(root)
    match = index.best("USABC1234567")

    assert first.discovered == 1
    assert first.indexed == 1
    assert second.unchanged == 1
    assert match is not None
    assert match.path == str(track)
    assert match.codec == "flac"
    assert match.lossless is True
    assert match.bit_depth == 16
    assert match.sample_rate_hz == 44100


def test_find_removes_stale_paths_instead_of_skipping(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    track = tagged_flac(root, "song.flac", "USABC1234567")
    index = LibraryIndex(tmp_path / "index.db")
    index.scan(root)
    track.unlink()

    assert index.find("USABC1234567") == []
    assert index.count() == 0


def test_find_refreshes_modified_tagged_file_on_demand(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    track = tagged_flac(root, "song.flac", "USABC1234567")
    index = LibraryIndex(tmp_path / "index.db")
    index.scan(root)
    audio = FLAC(track)
    audio["title"] = "Tag update"
    audio.save()

    matches = index.find("USABC1234567")

    assert len(matches) == 1
    assert matches[0].path == str(track)
    assert matches[0].mtime_ns == track.stat().st_mtime_ns


def test_duplicates_are_reported_without_file_mutation(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    first = tagged_flac(root, "one.flac", "USABC1234567")
    second = tagged_flac(root, "two.flac", "USABC1234567")
    index = LibraryIndex(tmp_path / "index.db")
    index.scan(root)

    duplicates = index.duplicates()

    assert [(isrc, len(matches)) for isrc, matches in duplicates] == [
        ("USABC1234567", 2)
    ]
    assert first.exists()
    assert second.exists()


def test_existing_quality_must_equal_or_exceed_selected_winner():
    cd = AudioQuality("flac", True, bit_depth=16, sample_rate_hz=44100)
    hires = AudioQuality("flac", True, bit_depth=24, sample_rate_hz=96000)
    lossy = AudioQuality("aac", False, bitrate_kbps=320)

    assert quality_at_least(cd, cd)
    assert quality_at_least(hires, cd)
    assert not quality_at_least(cd, hires)
    assert not quality_at_least(lossy, cd)


def test_unknown_indexed_channels_do_not_force_duplicate_lossless_download():
    indexed = AudioQuality(
        "flac", True, bit_depth=16, sample_rate_hz=44100, channels=None
    )
    advertised = AudioQuality(
        "flac", True, bit_depth=16, sample_rate_hz=44100, channels=2
    )

    assert quality_at_least(indexed, advertised)


def test_indexed_eac3_does_not_replace_requested_stereo_aac():
    indexed = AudioQuality("eac3", False, bitrate_kbps=768, spatial=False)
    requested = AudioQuality("aac", False, bitrate_kbps=320, spatial=False)

    assert not quality_at_least(indexed, requested)


def test_parent_scan_supersedes_nested_registered_root(tmp_path):
    root = tmp_path / "music"
    child = root / "Artist"
    child.mkdir(parents=True)
    tagged_flac(child, "song.flac", "USABC1234567")
    index = LibraryIndex(tmp_path / "index.db")

    index.scan(child)
    index.scan(root)

    assert [path for path, _ in index.roots()] == [str(root)]
