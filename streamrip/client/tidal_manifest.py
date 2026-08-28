"""Decode TIDAL BTS and DASH playback manifests."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from xml.etree import ElementTree

from ..multisource import AudioQuality, normalize_sample_rate


@dataclass(frozen=True, slots=True)
class TidalManifest:
    urls: tuple[str, ...]
    codec: str
    mime_type: str | None
    encryption_key: str | None
    restrictions: tuple[dict, ...]
    quality: AudioQuality


def _decode_payload(payload: str) -> str:
    try:
        return base64.b64decode(payload, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return payload


def _dash_urls(root: ElementTree.Element) -> tuple[tuple[str, ...], str, str | None]:
    representation = root.find(".//{*}Representation")
    if representation is None:
        raise ValueError("TIDAL DASH manifest has no Representation")

    codec = representation.get("codecs", "")
    mime_type = representation.get("mimeType")
    segment = representation.find("{*}SegmentTemplate")
    if segment is None:
        segment = root.find(".//{*}SegmentTemplate")
    if segment is None:
        raise ValueError("TIDAL DASH manifest has no SegmentTemplate")

    template = segment.get("media")
    if not template:
        raise ValueError("TIDAL DASH SegmentTemplate has no media URL")

    start_number = int(segment.get("startNumber", "0"))
    count = 0
    for item in segment.findall(".//{*}S"):
        count += int(item.get("r", "0")) + 1
    if count == 0:
        raise ValueError("TIDAL DASH manifest has no segments")
    return (
        tuple(template.replace("$Number$", str(i)) for i in range(start_number, start_number + count)),
        codec,
        mime_type,
    )


def parse_tidal_manifest(response: dict) -> TidalManifest:
    """Normalize a TIDAL playback-info response and its embedded manifest."""

    payload = response.get("manifest")
    if payload:
        decoded = _decode_payload(payload)
        if decoded.lstrip().startswith("<"):
            urls, codec, mime_type = _dash_urls(ElementTree.fromstring(decoded))
            manifest: dict = {}
        else:
            manifest = json.loads(decoded)
            urls = tuple(manifest.get("urls") or ())
            codec = str(manifest.get("codecs") or "")
            mime_type = manifest.get("mimeType")
    else:
        manifest = response
        urls = tuple(manifest.get("urls") or ())
        codec = str(manifest.get("codecs") or "")
        mime_type = manifest.get("mimeType")

    if not urls:
        raise ValueError("TIDAL manifest has no media URLs")

    actual_quality = str(response.get("audioQuality") or "").upper()
    audio_mode = str(response.get("audioMode") or "").upper()
    codec_lower = codec.casefold()
    lossless = actual_quality in {"LOSSLESS", "HI_RES", "HI_RES_LOSSLESS"} or (
        "flac" in codec_lower or "alac" in codec_lower
    )
    quality = AudioQuality(
        codec=codec or "unknown",
        lossless=lossless,
        bit_depth=response.get("bitDepth"),
        sample_rate_hz=normalize_sample_rate(response.get("sampleRate")),
        bitrate_kbps=response.get("bitrate"),
        channels=response.get("channels"),
        spatial=audio_mode not in {"", "STEREO"},
    )
    encryption_key = manifest.get("keyId")
    if manifest.get("encryptionType") == "NONE":
        encryption_key = None
    return TidalManifest(
        urls=urls,
        codec=codec,
        mime_type=mime_type,
        encryption_key=encryption_key,
        restrictions=tuple(manifest.get("restrictions") or ()),
        quality=quality,
    )

