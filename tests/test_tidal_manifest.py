import base64
import json

import pytest

from streamrip.client.tidal_manifest import parse_tidal_manifest


def encoded(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def test_parses_bts_json_and_actual_quality():
    manifest = encoded(
        json.dumps(
            {
                "mimeType": "audio/flac",
                "codecs": "flac",
                "encryptionType": "NONE",
                "urls": ["https://media/track.flac"],
            }
        )
    )
    result = parse_tidal_manifest(
        {
            "manifest": manifest,
            "audioQuality": "LOSSLESS",
            "audioMode": "STEREO",
            "bitDepth": 16,
            "sampleRate": 44100,
        }
    )
    assert result.urls == ("https://media/track.flac",)
    assert result.codec == "flac"
    assert result.encryption_key is None
    assert result.quality.lossless is True
    assert result.quality.sample_rate_hz == 44100


def test_parses_dash_segments_and_hires_profile():
    xml = """<?xml version="1.0"?>
    <MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
      <Period><AdaptationSet><Representation codecs="flac" mimeType="audio/mp4">
        <SegmentTemplate initialization="https://media/init.mp4"
                         media="https://media/$Number$.m4s" startNumber="1">
          <SegmentTimeline><S d="1" r="2"/><S d="1"/></SegmentTimeline>
        </SegmentTemplate>
      </Representation></AdaptationSet></Period>
    </MPD>"""
    result = parse_tidal_manifest(
        {
            "manifest": encoded(xml),
            "manifestMimeType": "application/dash+xml",
            "audioQuality": "HI_RES_LOSSLESS",
            "audioMode": "STEREO",
            "bitDepth": 24,
            "sampleRate": 96000,
        }
    )
    assert result.urls == (
        "https://media/init.mp4",
        *(f"https://media/{i}.m4s" for i in range(1, 5)),
    )
    assert result.mime_type == "audio/mp4"
    assert result.quality.rank > parse_tidal_manifest(
        {
            "urls": ["https://media/cd.flac"],
            "codecs": "flac",
            "audioQuality": "LOSSLESS",
            "bitDepth": 16,
            "sampleRate": 44.1,
        }
    ).quality.rank


def test_lossy_atmos_is_not_misclassified_as_lossless():
    result = parse_tidal_manifest(
        {
            "urls": ["https://media/atmos.m4a"],
            "codecs": "eac3",
            "audioQuality": "HIGH",
            "audioMode": "DOLBY_ATMOS",
            "bitrate": 768,
            "channels": 6,
        }
    )
    assert result.quality.lossless is False
    assert result.quality.spatial is True


def test_rejects_manifest_without_urls():
    with pytest.raises(ValueError, match="no media URLs"):
        parse_tidal_manifest({"manifest": encoded(json.dumps({"codecs": "flac"}))})
