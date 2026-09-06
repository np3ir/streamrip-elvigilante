import os
from types import SimpleNamespace

import pytest

from streamrip.media.album import PendingAlbum
from streamrip.metadata.album import AlbumInfo, AlbumMetadata
from streamrip.metadata.covers import Covers
from streamrip.metadata.track import TrackInfo, TrackMetadata


def metadata_for(source: str) -> TrackMetadata:
    album = AlbumMetadata(
        info=AlbumInfo(
            id=f"{source}-album",
            quality=2,
            container="FLAC",
            explicit=False,
            sampling_rate=44100,
            bit_depth=16,
        ),
        album="Álbum: Uno",
        albumartist="Ñandú",
        year="2024",
        genre=["Latin"],
        covers=Covers(),
        tracktotal=10,
        release_date="2024-03-02",
        release_type="ALBUM",
    )
    return TrackMetadata(
        info=TrackInfo(id=f"{source}-track", quality=2, explicit=True),
        title="Canción (Remix)",
        album=album,
        artist="Principal / Invitado",
        main_artists="Principal",
        featured_artists="Invitado",
        artist_separator=" / ",
        tracknumber=3,
        discnumber=1,
        composer=None,
        isrc="AA0000000001",
        version="Remix",
    )


@pytest.mark.parametrize("source", ["tidal", "deezer", "qobuz"])
def test_tiddl_placeholders_render_identically_for_every_service(source):
    track = metadata_for(source)

    folder = track.album.format_folder_path(
        r"{artist_initials}\{album.artist}\({album.date:%Y}) {album.title}"
    )
    filename = track.format_track_path(
        "{item.number:02}. {item.artists_with_features} - "
        "{item.title_version}{item.explicit:shortparens}"
    )

    assert folder == os.path.join("N", "Ñandú", "(2024) Álbum： Uno")
    assert filename == "03. Principal ／ Invitado - Canción (Remix) (explicit)"


def test_legacy_streamrip_placeholders_match_tiddl_namespace():
    track = metadata_for("tidal")

    legacy_folder = track.album.format_folder_path(
        r"{artist_initials}\{albumartist}\({year}) {title}"
    )
    tiddl_folder = track.album.format_folder_path(
        "{artist_initials}/{album.artist}/({album.date:%Y}) {album.title}"
    )
    legacy_track = track.format_track_path(
        "{tracknumber:02}. {artist} - {title}{explicit}"
    )
    tiddl_track = track.format_track_path(
        "{item.number:02}. {item.artists_with_features} - "
        "{item.title_version}{item.explicit:shortparens}"
    )

    assert legacy_folder == tiddl_folder
    assert legacy_track == tiddl_track


def test_regular_album_flow_preserves_long_unicode_template_hierarchy():
    track = metadata_for("tidal")
    track.album.album = "é" * 100
    config = SimpleNamespace(
        session=SimpleNamespace(
            downloads=SimpleNamespace(source_subdirectories=False),
            filepaths=SimpleNamespace(
                folder_format="{album.artist}/{album.title}",
                restrict_characters=False,
            ),
        )
    )
    pending = PendingAlbum(
        id="album",
        client=SimpleNamespace(source="tidal"),
        config=config,
        db=None,
    )

    path = pending._album_folder("library", track.album)

    assert path == os.path.join("library", "Ñandú", "é" * 100)
    assert len(path.encode("utf-8")) > 150
