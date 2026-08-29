import pytest

from streamrip.multisource import (
    AudioQuality,
    MatchKind,
    QualityCeiling,
    ServiceCandidate,
    TrackIdentity,
    choose_best,
    match_tracks,
    normalize_sample_rate,
)


def identity(source="tidal", source_id="1", **kwargs):
    defaults = dict(
        title="Canción de Prueba",
        artist="Artista",
        duration_seconds=240,
        isrc=None,
    )
    defaults.update(kwargs)
    return TrackIdentity(source=source, source_id=source_id, **defaults)


def candidate(source, quality):
    return ServiceCandidate(identity(source=source, source_id=source), quality)


def test_isrc_is_a_strong_match():
    left = identity(isrc="US-ABC-12-34567", title="Different title")
    right = identity(source="qobuz", isrc="us-abc-12-34567")
    assert match_tracks(left, right) is MatchKind.ISRC


def test_conflicting_isrc_is_not_rescued_by_similar_metadata():
    left = identity(isrc="USABC1234567")
    right = identity(source="deezer", isrc="USABC1234568")
    assert match_tracks(left, right) is MatchKind.NONE


def test_metadata_fallback_normalizes_accents_and_punctuation():
    left = identity(isrc=None)
    right = identity(
        source="qobuz",
        title="Cancion-de prueba!",
        artist="ARTISTA",
        duration_seconds=243,
    )
    assert match_tracks(left, right) is MatchKind.METADATA


def test_metadata_fallback_rejects_different_edition_or_duration():
    assert match_tracks(identity(), identity(source="qobuz", title="Canción de Prueba (Live)")) is MatchKind.NONE
    assert match_tracks(identity(), identity(source="qobuz", duration_seconds=244)) is MatchKind.NONE


def test_lossless_flac_wins_over_lossy_atmos():
    atmos = candidate(
        "tidal",
        AudioQuality(codec="eac3", lossless=False, bitrate_kbps=768, channels=6, spatial=True),
    )
    flac = candidate(
        "qobuz",
        AudioQuality(codec="flac", lossless=True, bit_depth=16, sample_rate_hz=44100, channels=2),
    )
    assert choose_best([atmos, flac]) is flac


def test_higher_lossless_resolution_wins():
    cd = candidate(
        "deezer",
        AudioQuality(codec="flac", lossless=True, bit_depth=16, sample_rate_hz=44100),
    )
    hires = candidate(
        "tidal",
        AudioQuality(codec="flac", lossless=True, bit_depth=24, sample_rate_hz=96000),
    )
    assert choose_best([cd, hires]) is hires


def test_equal_quality_prefers_tidal_then_deezer_then_qobuz():
    quality = AudioQuality(
        codec="flac",
        lossless=True,
        bit_depth=16,
        sample_rate_hz=44100,
        channels=2,
    )
    tidal = candidate("tidal", quality)
    deezer = candidate("deezer", quality)
    qobuz = candidate("qobuz", quality)

    assert choose_best([qobuz, deezer, tidal]) is tidal
    assert choose_best([qobuz, deezer]) is deezer


def test_service_priority_never_overrides_better_delivered_quality():
    tidal = candidate(
        "tidal",
        AudioQuality(codec="flac", lossless=True, bit_depth=16, sample_rate_hz=44100),
    )
    qobuz = candidate(
        "qobuz",
        AudioQuality(codec="flac", lossless=True, bit_depth=24, sample_rate_hz=96000),
    )

    assert choose_best([tidal, qobuz]) is qobuz


def test_custom_service_priority_changes_only_equal_quality_tie():
    quality = AudioQuality(
        codec="flac",
        lossless=True,
        bit_depth=16,
        sample_rate_hz=44100,
        channels=2,
    )
    tidal = candidate("tidal", quality)
    deezer = candidate("deezer", quality)

    assert choose_best(
        [tidal, deezer],
        service_priority=["deezer", "tidal", "qobuz"],
    ) is deezer


def test_bit_depth_ceiling_prefers_16_bit_over_higher_resolution():
    cd = candidate(
        "deezer",
        AudioQuality(codec="flac", lossless=True, bit_depth=16, sample_rate_hz=44100),
    )
    hires = candidate(
        "qobuz",
        AudioQuality(codec="flac", lossless=True, bit_depth=24, sample_rate_hz=192000),
    )

    assert choose_best([hires, cd], QualityCeiling(bit_depth=16)) is cd


def test_ceiling_falls_back_to_lossy_when_lossless_delivery_is_too_high():
    hires = candidate(
        "qobuz",
        AudioQuality(codec="flac", lossless=True, bit_depth=24, sample_rate_hz=96000),
    )
    aac = candidate(
        "tidal",
        AudioQuality(codec="aac", lossless=False, bitrate_kbps=320),
    )

    assert choose_best([hires, aac], QualityCeiling(bit_depth=16)) is aac


def test_ceiling_can_forbid_lossy_fallback():
    hires = candidate(
        "qobuz",
        AudioQuality(codec="flac", lossless=True, bit_depth=24, sample_rate_hz=96000),
    )
    aac = candidate(
        "tidal",
        AudioQuality(codec="aac", lossless=False, bitrate_kbps=320),
    )

    with pytest.raises(ValueError, match="quality ceiling"):
        choose_best(
            [hires, aac],
            QualityCeiling(bit_depth=16, fallback_to_lossy=False),
        )


def test_sample_rate_ceiling_excludes_unknown_and_above_ceiling_lossless():
    unknown = candidate("tidal", AudioQuality(codec="flac", lossless=True))
    high_rate = candidate(
        "qobuz",
        AudioQuality(codec="flac", lossless=True, bit_depth=16, sample_rate_hz=96000),
    )
    cd = candidate(
        "deezer",
        AudioQuality(codec="flac", lossless=True, bit_depth=16, sample_rate_hz=44100),
    )

    ceiling = QualityCeiling(bit_depth=16, sample_rate_hz=48000)
    assert choose_best([unknown, high_rate, cd], ceiling) is cd


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (0, None), (44.1, 44100), (96, 96000), (44100, 44100)],
)
def test_normalize_sample_rate(value, expected):
    assert normalize_sample_rate(value) == expected


def test_choose_best_requires_candidates():
    with pytest.raises(ValueError):
        choose_best([])
