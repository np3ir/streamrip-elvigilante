import os
import shutil

import pytest
from mutagen.flac import FLAC
from util import arun

from streamrip.metadata import (
    AlbumInfo,
    AlbumMetadata,
    Covers,
    TrackInfo,
    TrackMetadata,
    tag_file,
)

TEST_FLAC_ORIGINAL = "tests/silence.flac"
TEST_FLAC_COPY = "tests/silence_copy.flac"
test_cover = "tests/1x1_pixel.jpg"


def wipe_test_flac():
    audio = FLAC(TEST_FLAC_COPY)
    # Remove all tags
    audio.delete()
    audio.save()


@pytest.fixture()
def sample_metadata() -> TrackMetadata:
    return TrackMetadata(
        TrackInfo(
            id="12345",
            quality=3,
            bit_depth=24,
            explicit=True,
            sampling_rate=96,
            work=None,
        ),
        "testtitle",
        AlbumMetadata(
            AlbumInfo("5678", 4, "flac"),
            "testalbum",
            "testalbumartist",
            "1999",
            ["rock", "pop"],
            Covers(),
            14,
            3,
            "testalbumcomposer",
            "testcomment",
            compilation="testcompilation",
            copyright="(c) stuff (p) other stuff",
            date="1998-02-13",
            description="testdesc",
            encoder="ffmpeg",
            grouping="testgroup",
            lyrics="ye ye ye",
            purchase_date=None,
        ),
        "testartist",
        3,
        1,
        "testcomposer",
    )


def test_tag_flac_no_cover(sample_metadata):
    shutil.copy(TEST_FLAC_ORIGINAL, TEST_FLAC_COPY)
    wipe_test_flac()
    arun(tag_file(TEST_FLAC_COPY, sample_metadata, None))
    file = FLAC(TEST_FLAC_COPY)
    assert file["title"][0] == "testtitle"
    assert file["album"][0] == "testalbum"
    assert file["composer"][0] == "testcomposer"
    assert file["comment"][0] == "testcomment"
    assert file["artist"][0] == "testartist"
    assert file["albumartist"][0] == "testalbumartist"
    # tiddl parity: DATE carries the year alone, no YEAR/TRACKTOTAL tags,
    # unpadded track/disc numbers
    assert "year" not in file, file["year"]
    assert file["genre"][0] == "rock, pop"
    assert file["tracknumber"][0] == "3"
    assert file["discnumber"][0] == "1"
    assert file["copyright"][0] == "© stuff ℗ other stuff"
    assert "tracktotal" not in file, file["tracktotal"]
    assert file["date"][0] == "1999"
    assert "purchase_date" not in file, file["purchase_date"]
    os.remove(TEST_FLAC_COPY)


def test_tag_flac_cover(sample_metadata):
    shutil.copy(TEST_FLAC_ORIGINAL, TEST_FLAC_COPY)
    wipe_test_flac()
    arun(tag_file(TEST_FLAC_COPY, sample_metadata, test_cover))
    file = FLAC(TEST_FLAC_COPY)
    assert file["title"][0] == "testtitle"
    assert file["album"][0] == "testalbum"
    assert file["composer"][0] == "testcomposer"
    assert file["comment"][0] == "testcomment"
    assert file["artist"][0] == "testartist"
    assert file["albumartist"][0] == "testalbumartist"
    # tiddl parity: DATE carries the year alone, no YEAR/TRACKTOTAL tags,
    # unpadded track/disc numbers
    assert "year" not in file, file["year"]
    assert file["genre"][0] == "rock, pop"
    assert file["tracknumber"][0] == "3"
    assert file["discnumber"][0] == "1"
    assert file["copyright"][0] == "© stuff ℗ other stuff"
    assert "tracktotal" not in file, file["tracktotal"]
    assert file["date"][0] == "1999"
    with open(test_cover, "rb") as img:
        assert file.pictures[0].data == img.read()
    assert "purchase_date" not in file, file["purchase_date"]
    os.remove(TEST_FLAC_COPY)
