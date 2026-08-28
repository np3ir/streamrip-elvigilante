# Streamrip ElVigilante — AI handoff context

Last updated: 2026-08-28 (America/La_Paz)

## Objective

Evolve `streamrip-elvigilante` 2.2.8 into a robust multi-source downloader:

1. Make its TIDAL authentication, quality cascade, download pipeline, and file construction as reliable and efficient as (or better than) sibling project `../tiddl-elvigilante`.
2. Preserve Deezer and Qobuz compatibility.
3. Match equivalent recordings across TIDAL, Qobuz, and Deezer.
4. Compare actual available audio properties and download the highest-fidelity candidate.

Default fidelity policy agreed during implementation: lossless stereo FLAC outranks lossy spatial/Atmos. Among lossless candidates, prefer bit depth, then sample rate, then bitrate. ISRC is the strong recording identity; title + artist + duration (3-second tolerance) is a conservative fallback when ISRC is absent.

## Environment

- Repository: `G:\My Drive\Backups\zhome-2026-07-25\Streamrip`
- Branch: `codex/multisource-comparison`, created from `main` at `28f634a`
- Base version: `2.2.8`
- Development Python: 3.13
- Isolated development environment: `%LOCALAPPDATA%\streamrip-elvigilante-venv`
- Global `rip 2.1.0` under Python 3.13 must remain untouched until explicitly authorized.
- Repository lives in Google Drive. Do not put virtual environments inside it; creating many small files is extremely slow.
- Sibling reference implementation: `..\tiddl-elvigilante` 1.5.4. Read its `AGENTS.md` before changing anything in that sibling repository. It is a reference only; do not modify/release it without explicit authorization.

## Committed multi-source implementation

Checkpoint commit: `254c33c feat: add multisource quality comparison`. The commit is local on `codex/multisource-comparison`; no push occurred.

### Multi-source foundation

New `streamrip/multisource.py`:

- `TrackIdentity`, `AudioQuality`, and `ServiceCandidate` service-neutral models.
- `match_tracks()` uses exact normalized ISRC first. Conflicting populated ISRCs never fall back to fuzzy metadata.
- Metadata fallback normalizes Unicode/accent/punctuation and requires title, artist, and duration within three seconds.
- `choose_best()` implements the fidelity-first policy.
- `normalize_sample_rate()` normalizes service values expressed in kHz or Hz.

Tests: `tests/test_multisource.py`.

### TIDAL manifest and quality work

New `streamrip/client/tidal_manifest.py`:

- Parses base64 BTS/JSON manifests and DASH/XML manifests.
- Extracts ordered media segment URLs.
- Captures codec, MIME type, encryption information, restrictions, and actual audio properties.
- Correctly treats E-AC-3 Atmos as lossy/spatial and FLAC/ALAC as lossless.

Changes in `streamrip/client/tidal.py`:

- `HI_RES_LOSSLESS` is quality level 4; `TidalClient.max_quality = 4`.
- A single de-duplicated cascade replaces separate FLAC and AAC request loops.
- A downgraded AAC response does not stop the cascade while a lossless tier remains to try.
- The best lossy response is retained as a final fallback.
- Removed `_flac_downloaded`, which marked a track before its bytes were successfully written.

Changes in `streamrip/client/downloadable.py`:

- `TidalDownloadable` can carry actual normalized quality.
- Supports one URL or multiple ordered DASH segment URLs.
- Segment transfer is batched (8 at a time), bounded in memory, written to `.part`, and atomically renamed.

Changes in TIDAL metadata adapters:

- `streamrip/metadata/track.py` and `streamrip/metadata/album.py` recognize `HI_RES_LOSSLESS`.
- Prefer delivered `bitDepth` and `sampleRate`; use estimates only when omitted.

Tests: `tests/test_tidal_manifest.py` and `tests/test_tidal_quality.py`.

### TIDAL file construction and container normalization

New `streamrip/audio_container.py` and integration in `streamrip/media/track.py`:

- Detects ISO Base Media/MP4 from the `ftyp` bytes rather than trusting the filename or requested quality.
- For a delivered lossless TIDAL stream inside MP4, invokes FFmpeg with stream copy (`-c:a copy`), so audio is not transcoded.
- Writes extraction output to a same-directory temporary file, validates non-empty output, and atomically replaces the destination.
- Keeps the original source on extraction failure and cleans extraction temporaries.
- Runs blocking FFmpeg through `asyncio.to_thread(subprocess.run)`; this works with both Selector and Proactor event loops on Windows.
- TIDAL segmented transfers use bounded batches, preserve manifest order even when HTTP responses complete out of order, publish via `.part` + atomic rename, and remove partials after an HTTP failure.

Tests: `tests/test_audio_container.py` and `tests/test_tidal_segment_download.py`. The tests use generated temporary audio and a loopback aiohttp server; they do not contact a music service or the user's library.

### Service candidate adapters and concurrent comparison

New `streamrip/client/candidate.py` and `Client.get_candidate()`:

- Convert raw TIDAL, Qobuz, and Deezer track metadata into the common `TrackIdentity` model.
- Resolve a stream URL/manifest without transferring media and normalize the quality actually available.
- Qobuz uses technical fields from `track/getFileUrl`; unknown Hi-Res details are not invented.
- Deezer maps the selected tier to FLAC 16/44.1, MP3 320, or MP3 128.
- TIDAL uses the delivered manifest profile.

New `streamrip/comparison.py`:

- Searches TIDAL, Qobuz, and Deezer concurrently.
- Prefers exact ISRC and verifies matches again after fetching full metadata.
- Rejects conflicting populated ISRCs before stream inspection.
- Isolates failures per service and returns candidates, selected source, and errors.

Tests: `tests/test_service_candidates.py` and `tests/test_comparison.py`.

### CLI comparison and opt-in best-source download

New command: `rip compare SOURCE TRACK_ID`, optionally repeating `--service` to limit compared services.

- Logs into only the requested/reference services.
- Reuses the inspected reference candidate instead of requesting its manifest twice.
- Displays service, match type, normalized quality, winner, and isolated service errors.
- It is preview-only by default. Media transfer occurs only with explicit `--download-best`.

Important observed side effect: invoking the real `rip compare --help` on 2026-08-27 triggered Streamrip's pre-existing group-level config migration and updated `%APPDATA%\streamrip\config.toml` from schema 2.0.6 to 2.2.0. No backup file was created by the existing updater. No credentials or media were changed. Do not revert or further alter the user's config without explicit authorization. Future help tests should inspect the Click command object or use a temporary `--config-path`.

## Config-migration safety

Committed as `83a5f99 fix: protect config migrations` (local only; not pushed).

- `streamrip/rip/cli.py` detects help-only invocations before logging, config loading, or migration, preventing `rip compare --help` and similar help commands from mutating configuration.
- `streamrip/config.py` creates a non-overwriting backup (`.bak`, `.bak.1`, and so on) before a real migration.
- Config replacement is now crash-safer: write and `fsync` a same-directory temporary file, then atomically replace the original.
- `tests/test_compare_cli.py` covers early help detection and invokes the Click command against a temporary old-schema config to prove help leaves it byte-for-byte unchanged without creating a backup.
- `tests/test_config.py` verifies preservation of an existing backup and creation of the next numbered backup.
- Manual real-executable validation passed: `rip --config-path <temporary-old-config> compare --help` left SHA-256 `CECA9A4A8E756C3F64BC95E78B9E88F615102109DE805D75B0025F831D63845D` unchanged and created no backup. The temporary probe was removed afterward.
- Targeted validation: `14 passed` for `tests/test_config.py tests/test_compare_cli.py`; Ruff clean.

## Validation baseline

Latest full run after the opt-in best-source changes:

- `149 passed`
- `7 skipped` (credentials/integration tests unavailable)
- `0` runtime warnings in the final suite summary
- Ruff clean on all modified/new files
- `git diff --check` clean except informational LF-to-CRLF warnings on Windows

Commands:

```powershell
& "$env:LOCALAPPDATA\streamrip-elvigilante-venv\Scripts\python.exe" -m pytest
& "$env:LOCALAPPDATA\streamrip-elvigilante-venv\Scripts\ruff.exe" check <changed files>
```

## Next work

1. Perform controlled live comparison using available service credentials, preview-only first.
2. Verify delivered media properties with a single authorized temporary-directory download before considering installation changes.

## Committed opt-in best-source download

Committed as `16d01df feat: download best matching source` (local only; not pushed).

- `rip compare SOURCE TRACK_ID` remains preview-only by default.
- New `--download-best` flag queues exactly the selected candidate through the existing `Main.add_by_id(..., "track", ...)` and `Main.rip()` pipeline, preserving normal metadata, paths, retries, tagging, database, and TIDAL container normalization.
- The comparison table is shown before opt-in download begins.
- `streamrip/comparison.py::download_selected()` rejects empty reports and queues only the highest-fidelity candidate.
- Tests prove only the winner is queued and the no-candidate case cannot start a download.
- Real `rip compare --help` shows the opt-in flag and exits without configuration migration.
- Validation: Ruff clean; full suite `139 passed, 7 skipped, 1` pre-existing warning. No real service login or media download was performed.

## Committed TIDAL request safety and token refresh

Committed as `90ac62a fix: harden tidal request pacing and refresh` (local only; not pushed).

- New `streamrip/client/request_budget.py::SharedRequestBudget` provides an async fixed-interval request budget with one lock, no initial wait, injected clock/sleeper/jitter for deterministic tests, and a count of admitted real API requests.
- `TidalClient` accepts an optional shared budget and otherwise creates one per client/run from the effective `requests_per_minute` setting. All `_api_request` attempts, including retries, consume one budget slot.
- The older `_rate_lock`, `_last_request_time`, and inline spacing calculation were removed; adaptive 429 delay and bounded connection semaphore remain compatible.
- Fixed a concrete 401 bug: `_api_request` now forces token refresh even when the locally recorded expiry is still more than one hour away.
- Forced refresh carries the access token that actually failed. Under `auth_lock`, a second concurrent 401 observes that another coroutine already replaced that token and avoids a duplicate refresh request.
- New tests: `tests/test_request_budget.py` and `tests/test_tidal_auth.py` cover concurrent spacing, safe default RPM, forced 401 refresh, and concurrent-refresh deduplication.
- Validation: directed tests `7 passed`; Ruff clean; full suite `143 passed, 7 skipped, 1` pre-existing warning. No real service traffic or media download was performed.

## Committed TIDAL 429 circuit breaker

Committed as `49fe134 fix: stop sustained tidal rate-limit retries` (local only; not pushed).

- `RateLimitGuard` counts HTTP 429 responses for the current TIDAL client/run and trips once at a deliberately tolerant default of 12 strikes.
- A few transient 429 responses continue through the existing `Retry-After` and adaptive-backoff path.
- The response that reaches the threshold raises `TidalRateLimitError` before another retry is scheduled; every later TIDAL API call fails immediately without network access.
- Track/video playback fallback paths explicitly preserve this safety exception rather than hiding it as an ordinary unavailable-quality fallback.
- Tests cover exact one-shot trip semantics, invalid thresholds, pre-network rejection after trip, and a real internal 429-response path that trips before retrying.
- Validation: Ruff clean; full suite `147 passed, 7 skipped, 1` pre-existing warning. No real service traffic or media download was performed.

## Committed configuration-source correction

Committed as `8976012 fix: honor selected configuration in main` (local only; not pushed).

- Removed `Main`'s hard-coded secondary read of `%APPDATA%/streamrip/config.toml`; `Main` now exclusively consumes the already-loaded `Config` instance, so explicit `--config-path` and CLI session overrides remain authoritative.
- Download folder and filename formats are no longer silently overwritten during `Main` construction.
- Database paths come from `config.session.database`, with backward-compatible fallback under the configured download folder only when a path is empty.
- `downloads_enabled`, `failed_downloads_enabled`, and `isrc_enabled` now select real databases or `db.Dummy()` as configured; parent directories are created only for enabled databases.
- New `tests/test_main_config.py` places a conflicting config under a fake AppData directory and proves it is ignored, verifies exact configured database paths, and covers all disabled database backends.
- Validation: directed tests `5 passed`; Ruff clean; full suite `149 passed, 7 skipped, 1` pre-existing warning. No real service traffic or media download was performed.

## Committed warning and aiohttp-auth cleanup

Committed as `55ee197 test: remove internal async warning` (local only; not pushed).

- Corrected `test_latest_streamrip_version_creates_session` to model aiohttp's synchronous context-manager factories and asynchronous enter/exit/JSON methods accurately; the test now asserts the parsed release result instead of swallowing exceptions.
- Replaced deprecated `aiohttp.BasicAuth` construction in the TIDAL refresh flow with `aiohttp.encode_basic_auth()` and the recommended `Authorization` header.
- Validation: Ruff clean; full suite `149 passed, 7 skipped` with no runtime-warning summary. The remaining Click `MultiCommand` deprecation appears only as a dependency log during collection.

## Safety and decision constraints

- Never store access tokens, ARLs, app secrets, or private configuration in this file or tests.
- Do not download real media into the user's music library during tests; use temporary directories/local servers.
- Do not update the global `rip 2.1.0`, publish, push, merge, tag, or create a release without explicit authorization.
- Preserve existing Deezer and Qobuz behavior; changes require regression tests.
- Treat advertised quality as a hint. Selection must ultimately rely on the delivered manifest/file properties.

## Working tree expected at this handoff

The multi-source foundation is committed in `254c33c`, migration safety in `83a5f99`, opt-in best-source download in `16d01df`, TIDAL request/refresh safety in `90ac62a`, the 429 circuit breaker in `49fe134`, configuration-source correction in `8976012`, and warning/auth cleanup in `55ee197`. Only this memory update is currently modified.

Repository-local Git identity is configured as the existing project author. No global identity was changed and no push occurred.
