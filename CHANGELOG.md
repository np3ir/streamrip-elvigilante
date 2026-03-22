# Changelog

All notable changes to **Streamrip — ElVigilante Edition** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [2.2.8] — ElVigilante Edition

### Added

- **UPC metadata support** (`metadata/album.py`, `metadata/tagger.py`)
  — `AlbumMetadata` now includes a `upc` field; the tagger embeds it in downloaded files.

### Fixed

- **Long album folder names** (`media/album.py`)
  — Folder names exceeding 150 characters are now truncated to prevent "path too long"
  errors on Windows and network shares.

---

## [2.2.7] — ElVigilante Edition

### Fixed

- **Tidal URLs with `/u` suffix** (`rip/parse_url.py`)
  — URLs like `tidal.com/track/123/u` no longer break the URL regex parser.

- **Deezer dynamic link format** (`rip/parse_url.py`)
  — Added support for `link.deezer.com/s/...` short links in addition to
  the existing `deezer.page.link/...` format.

---

## [2.2.6] — ElVigilante Edition

### Added

- **Tidal `HI_RES_LOSSLESS` quality** (`client/tidal.py`)
  — Quality level 4 (`HI_RES_LOSSLESS` / FLAC Best) added to `QUALITY_MAP`.

### Fixed

- **Deezer URL redirects** (`client/deezer.py`)
  — Added `_resolve_redirect()`: when Deezer returns `DataException` for a moved
  album, playlist, or artist, streamrip now follows the redirect automatically
  and retries with the new ID.

---

## [2.2.5] — ElVigilante Edition

### Added

- **Playlist contributor enrichment via GW API** (`client/deezer.py`)
  — Playlist tracks are enriched concurrently using Deezer's internal GW API
  (`SNG_CONTRIBUTORS`) to recover featured artists omitted by the public endpoint.

- **Deezer download retry with `.part` files** (`client/downloadable.py`)
  — Failed downloads retry up to 3 times with exponential back-off. Files are
  written to a `.part` temporary path and renamed on success, so incomplete files
  are never left on disk.

- **Null-byte stripping** (`client/downloadable.py`)
  — Leading null bytes in the first encrypted chunk are stripped before decryption,
  fixing corrupted files on certain CDN responses.

### Fixed

- **Album artist uses only primary artist** (`metadata/album.py`)
  — `albumartist` / folder name now uses `artist.name` (primary) instead of a
  sorted multi-artist list, preventing "Beele / Shakira" style folder names.

- **Feat artist recovery from title** (`metadata/track.py`)
  — When `contributors` is missing and the title contains `(feat. …)`, the
  featured artists are parsed out and appended to the track artist field.

- **Geolocation fallback** (`client/deezer.py`)
  — Any download URL failure (not only `WrongGeolocation`) now triggers the
  fallback track ID retry.

- **UTF-8 console output on Windows** (`rip/cli.py`)
  — Forces UTF-8 encoding on startup to prevent `UnicodeEncodeError` for tracks
  with fullwidth or special characters in their filenames.

- **Version checker points to fork** (`rip/cli.py`)
  — Update notifications now check `Np3ir/streamrip-elvigilante` releases instead
  of the upstream PyPI package.

---

## [2.2.4] — ElVigilante Edition

### Fixed

- **`fetch_lrc` exception scope** (`media/lyrics.py`)
  — Catch `aiohttp.ClientResponseError` and `json.JSONDecodeError` in addition to the
  existing `aiohttp.ClientError`, `asyncio.TimeoutError`, and `NonStreamableError`.
  Only truly unexpected failures now propagate; lyrics retrieval never crashes a download.

- **`DeezerClient.get_lyrics` debug log** (`client/deezer.py`)
  — When `LYRICS_SYNC_JSON` fails to parse, the debug message now includes a truncated
  snippet of the raw value (first 80 chars or its type) to make diagnosis easier.

---

## [2.2.3] — ElVigilante Edition

### Added

- **Dedicated lyrics module** (`media/lyrics.py`)
  — `fetch_lrc` extracted from `media/track.py` into a standalone module so that
  `PendingTrack`, `PendingSingle`, and `PendingPlaylistTrack` all share a single,
  tested implementation.

- **Adaptive rate-limit delay for Tidal** (`client/tidal.py`)
  — A `_rate_limit_delay` float (initially `0.0`) is maintained per client instance.
  Every HTTP 429 response increments it by `1.0 s` (max `5.0 s`); every successful
  JSON response decrements it by `0.1 s` (floor `0.0 s`). This delay is applied before
  the fixed-interval gate on every request, mirroring the strategy used in tiddl and
  tidmon for a consistent cross-tool behaviour.

- **`Deezer.get_lyrics` — JSON string handling** (`client/deezer.py`)
  — `LYRICS_SYNC_JSON` fields returned as a JSON string (rather than a pre-parsed list)
  are now decoded with `json.loads`. A `json.JSONDecodeError` is caught and logged at
  `DEBUG` level so a malformed field never prevents lyrics from falling through to the
  plain-text path.

---

## [2.2.2] — ElVigilante Edition

### Changed

- **Tidal rate limiting — replaced token bucket with fixed-interval async gate**
  (`client/tidal.py`)
  — `aiolimiter.AsyncLimiter` (token bucket) was removed. An `asyncio.Lock` now
  serialises all coroutines through a single fixed-interval gate (`60 / rpm` seconds).
  Per-request jitter (`random.uniform(0, 0.3)`) is added inside the lock so the pattern
  is unpredictable to the API. This eliminates the burst behaviour that caused 429 errors
  at the start of large downloads.

- **Configurable `requests_per_minute`** (`client/tidal.py`)
  — The Tidal client already accepted `requests_per_minute` from `[downloads]`; the
  default is now a safe `60` rpm enforced through the new lock-based limiter.

---

## [2.2.1] — ElVigilante Edition

### Added

- **Configurable `artist_separator`** (`[metadata]` in config.toml)
  — Lets you choose how multiple artists are joined in file names and audio tags
  (`", "`, `" & "`, `" / "`, `"; "`, etc.). Applies to both the embedded
  `ARTIST`/`ALBUMARTIST` tag and the generated file name. Works for Qobuz, Tidal and Deezer.
  Default is `", "` — no behaviour change for existing configs.

- **`AlbumMetadata.from_qobuz` joins all album artists**
  — Previously only `artists[0]` was used. All artists in the Qobuz `artists` array are now
  joined with `artist_separator`, consistent with track-level artist handling.

- **`artist_separator` threaded through `from_album_resp`**
  — `AlbumMetadata.from_album_resp` now accepts and forwards `artist_separator` to
  the source-specific parsers, so albums resolved via `PendingAlbum` also respect the setting.

- **Internal methods have no default for `artist_separator`**
  — `from_qobuz`, `from_tidal`, `from_deezer` and `from_tidal_playlist_track_resp`
  no longer have a hardcoded `= ", "` default. The default lives only on the public
  dispatchers (`from_resp`, `from_track_resp`, `from_album_resp`), preventing silent drift
  between the config value and the hardcoded fallback.

- **`_resolve_track_folder()` accepts `str | os.PathLike[str]`** (`media/playlist.py`)
  — The helper that computes the track folder in a playlist now accepts `pathlib.Path`
  objects in addition to `str`, using `os.fspath()` internally.

- **Improved `rip()` warning** (`media/track.py`)
  — When a track is not downloaded after all retries, the log message now includes the
  track ID (`id=…`) and the configured retry count (`after N retries`) to aid debugging.

- **`max_retries` normalised to `int`** (`config.py`)
  — If `max_retries` comes as a string in the TOML (e.g. `"3"`), it is automatically
  converted to an integer. Negative values are reset to 0 with a warning.

- **`test_semaphore_behavior.py`** — async tests using `@pytest.mark.asyncio`
  — Replaces the previous `asyncio.run()` approach.

- **`source` and `extension` on `_FailingDownloadable`** (`test_track_retry_behavior.py`)
  — Attributes required so `set_failed` never raises `AttributeError` when retries are
  exhausted.

- **Safe post-process guard** (`media/track.py`)
  — If the file does not exist on disk after all retries, `rip()` now logs a descriptive
  warning and returns instead of crashing in `postprocess()`.

- **`_resolve_track_folder()` extracted** (`media/playlist.py`)
  — Folder-resolution logic for playlist tracks moved to a private helper, removing
  duplicated code.

- **Full exponential back-off** (`client/downloadable.py`)
  — Retries wait `retry_delay * 2^attempt` seconds, capped at `max_wait`. DNS failures
  and network errors are retried correctly.

- **Tidal credentials via environment variables**
  — `TIDAL_CLIENT_ID` and `TIDAL_CLIENT_SECRET` can be exported instead of storing
  them in `config.toml`.

- **Test suite** (69 tests across 5 modules)
  — `test_config.py`, `test_db.py`, `test_filepath_utils.py`,
  `test_semaphore_behavior.py`, `test_track_retry_behavior.py`.

- **TiDDL-style colour output**
  — Green for successful downloads, yellow for skipped, red for errors.

- **Full English documentation**
  — README.md, CHANGELOG.md and all files in `docs/` are now in English.

### Fixed

- **Duplicate folder in playlists** with `set_playlist_to_album = true`
  — The album/playlist name is no longer added as a sub-folder when
  `set_playlist_to_album` is enabled (it was being used as both the root folder name
  and a sub-folder, resulting in duplication).

- **`AlbumMetadata` repr in folder names** (`media/playlist.py`)
  — Album folders in playlists were showing the `repr()` of the `AlbumMetadata` object
  instead of the clean album title.

- **Crash in `postprocess()` on download failure**
  — If all retries were exhausted and the file did not exist, the process continued
  into `postprocess()` and crashed. It is now detected and skipped with a warning.

- **`assert` replaced by proper exceptions**
  — Avoids unexpected `AssertionError` in production.

- **Semaphore with conflicting configuration**
  — Setting `concurrency=False` with `max_connections > 1` no longer crashes; it emits
  a descriptive warning instead.

### Changed

- **Flat package layout**
  — Modules live directly under `site-packages/streamrip/` in addition to the standard
  repository layout under `streamrip/streamrip/`.

- **`config.toml` version `2.0.6` baseline**
  — Added `max_retries`, `retry_delay`, `max_wait` under `[downloads]` and
  `artist_separator` under `[metadata]`.

---

## [2.0.6] — nathom/streamrip (upstream base)

Base from which this fork was created. See the
[upstream project history](https://github.com/nathom/streamrip/releases)
for prior changes.

---

> This fork maintains full compatibility with the upstream `config.toml` format.
> All new settings have defaults that preserve the original behaviour.
