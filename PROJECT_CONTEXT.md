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

Latest full run after the explicit login-command foundation:

- `161 passed`
- `7 skipped` (credentials/integration tests unavailable)
- `0` runtime warnings in the final suite summary
- Ruff clean on all modified/new files. A separate whole-repository Ruff run reports one pre-existing `RUF036` ordering issue in `streamrip/media/semaphore.py:10`; it is unrelated to the current comparison change.
- `git diff --check` clean except informational LF-to-CRLF warnings on Windows

Commands:

```powershell
& "$env:LOCALAPPDATA\streamrip-elvigilante-venv\Scripts\python.exe" -m pytest
& "$env:LOCALAPPDATA\streamrip-elvigilante-venv\Scripts\ruff.exe" check <changed files>
```

## Next work

1. Remove the two exact temporary live-validation directories after user confirmation or through an allowed safe cleanup mechanism; both paths are listed below.
2. Add Deezer browser-assisted login (WebView2 on Windows) while retaining the validated hidden manual-ARL path.
3. Investigate and restore the broken standalone `rip search` CLI paths: both interactive and output-file modes currently call missing `Main` methods. This is separate from `rip compare`, which works.
3. Decide whether to prepare a side-by-side 2.2.8 installation without replacing global `rip 2.1.0`.

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

## Controlled live comparison and restored device authentication

Committed as `541e9a3 fix: restore tidal device authentication` (local only; not pushed).

- User supplied TIDAL reference track `524417109` for a preview-only comparison.
- `rip compare tidal 524417109` was executed twice without `--download-best`; no audio was transferred.
- The first two attempts stopped during TIDAL token refresh with HTTP 401 `invalid_client`; the saved refresh token was associated with a retired/unavailable OAuth client.
- With explicit user authorization, the official device flow was restored and approved. The refreshed session was persisted through the dedicated token store and config save path; no secret values are recorded here.
- OAuth client selection and refresh/device-token request bodies now match sibling `tiddl-elvigilante`. `invalid_client` is classified as `AuthenticationError` and automatically falls back to device authorization for an explicitly requested/reference service.
- `TidalClient._get_device_code()` and `_get_auth_status()` were restored with pending/success handling and tests.
- Secondary services in `rip compare` now use `prompt_on_missing=False`: missing/invalid credentials are isolated as service errors and never trigger an unexpected credential prompt. The initial all-service attempt exposed this issue by prompting for a Deezer ARL; it was cancelled without entering or changing an ARL.
- Live preview `rip compare --service tidal tidal 524417109` succeeded and reported `FLAC / lossless / 24-bit / 44.1 kHz` with an ISRC match. No audio was transferred.
- A subsequent all-service preview succeeded without credential prompts: TIDAL remained selected; no Qobuz match was returned and Deezer was reported unavailable due to authentication. No audio was transferred.
- Validation: directed auth/config/CLI tests `13 passed`; Ruff clean; full suite `153 passed, 7 skipped` with no runtime-warning summary.

## Controlled live download and DASH construction fix

Committed as `e76eaa5 fix: assemble complete tidal dash streams` (local only; not pushed).

- User explicitly authorized one real `--download-best` test for TIDAL track `524417109`, isolated with a unique temporary download folder and `--no-db`.
- `--no-db` now disables downloads, failed-downloads, and ISRC databases together; regression coverage proves no database backend can be written during such a test.
- The first live file exposed an actual DASH assembly defect: only media fragments were concatenated, producing an MP4 fragment stream with no initialization metadata; FFprobe rejected it with missing `tfhd`/header errors.
- `tidal_manifest._dash_urls()` now prepends `SegmentTemplate@initialization` before all numbered media segments. The manifest regression test pins exact initialization-first ordering.
- Repeating the authorized download after the fix succeeded and the normal TIDAL container normalization extracted a valid FLAC without transcoding.
- FFprobe verification of the corrected file: native FLAC, 24-bit (`bits_per_raw_sample=24`), 44,100 Hz, 2 channels, duration 197.872676 s, size 36,606,842 bytes. The file also contains embedded MJPEG cover art. SHA-256 was `B1B3F499E2BDD3B65AE273776C4A3AAFDE60A78C4E9AA2652FB606CD38BEEC80`.
- Validation: Ruff clean; full suite `154 passed, 7 skipped`. No user-library database or destination was used.
- Cleanup risk/status: automated recursive removal was rejected by the execution policy, so two exact temporary directories remain under `%TEMP%`: `streamrip-live-validation-1271bd2f7aaa47388251d69bbd73ddd1` (invalid first artifact) and `streamrip-live-validation-fixed-a9998064735747169b53c645e826e99c` (verified FLAC). They are outside the repository and user music library.

## TIDAL single-album metadata correction

Committed as `97a2c2d fix: resolve tidal single album metadata` (local only; not pushed).

- The successful live download revealed a metadata-path defect: the single folder used `1970-01-01` from the summarized track's `streamStartDate` even though the release is current.
- `PendingSingle` now fetches the full TIDAL album response using the embedded album ID and builds `AlbumMetadata` from that authoritative response. If the album request fails, it logs at debug level and retains the previous embedded-track fallback.
- The fallback date priority now prefers `releaseDate`, normalized `date`, and embedded album `releaseDate` before `streamStartDate`/`dateAdded`.
- A live metadata-only query for track `524417109` resolved album `524417108`, title `real still loving you`, and authoritative release date `2026-05-13`; no audio was downloaded in this verification.
- New regression test proves a bogus 1970 track start date cannot override the full album's 2026 release date.
- Validation: Ruff clean; full suite `155 passed, 7 skipped`.

## ISRC-first cross-service discovery

Committed as `3d0d832 feat: search cross-service tracks by isrc` (local only; not pushed). Documentation checkpoint: `cb9abdc docs: record isrc-first live comparison`.

- Secondary-service discovery now searches the exact reference ISRC first, then falls back to `artist + title` metadata search.
- Results returned by both queries are de-duplicated by service track ID before any stream manifest/file-URL inspection, preventing repeated quality-resolution requests.
- Candidate ordering continues to prefer an exact ISRC match over metadata fallback and re-verifies identity after full candidate resolution.
- Controlled live preview `rip compare tidal 524417109` completed without downloading audio. TIDAL remained the winner at FLAC/lossless/24-bit/44.1 kHz; Qobuz returned no equivalent result even with ISRC-first discovery, and Deezer remained unavailable because no valid authentication was configured. No credential prompt appeared.
- Validation: comparison tests `9 passed`; full suite `156 passed, 7 skipped`; Ruff clean on the two changed files.

## Deezer authenticated live validation

- On 2026-08-28 the user supplied Deezer access. It was validated through hidden interactive input and saved only to Streamrip's private local configuration; the ARL is intentionally absent from this file, repository history, tests, and command arguments.
- Preview-only `rip compare tidal 524417109` completed with all three clients available and no media download. TIDAL remained the sole candidate and winner at FLAC/lossless/24-bit/44.1 kHz; Qobuz and Deezer returned no valid equivalent recording.
- A focused catalog query confirmed the reference ISRC is `UPL524417109`. Deezer returned zero results for both the raw and field-qualified ISRC. Its artist/title query returned one unrelated track with a conflicting ISRC, so the comparator correctly rejected it before stream-quality inspection.
- An attempted non-downloading `rip search` probe exposed a separate pre-existing CLI defect: `cli.py` calls missing `Main.search_output_file()` and `Main.search_interactive()` methods. No product code was changed during credential validation; this defect is now a next-work item.

## Explicit service-login foundation

Current uncommitted implementation in `streamrip/rip/login.py`, `streamrip/rip/cli.py`, `streamrip/client/qobuz.py`, and `tests/test_login.py`:

- New `rip login` command group with `qobuz`, `deezer`, `status`, and `logout` commands.
- Qobuz supports hidden email/password input or `--token` user-ID/token input. After a successful email/password exchange, only the returned user ID and `user_auth_token` are persisted; neither the clear password nor its MD5 is written to disk.
- Qobuz app ID and reusable app-secret candidates remain automatically discovered through the existing web-player extraction path. Manual token login remains available, matching QobuzDownloaderX's two login modes.
- Removed Qobuz credential values and full login responses from debug logs and authentication exceptions.
- Deezer manual ARL entry is hidden and validated before replacing the credential stored on disk. Browser-assisted login remains the next phase; manual ARL must remain as a fallback.
- `rip login status` reports only whether TIDAL/Qobuz/Deezer are configured. `rip login logout SERVICE` removes the selected user credential while preserving reusable Qobuz app metadata.
- Real command-object validation shows all three current services as configured without printing credential values. No login, logout, download, or user-config mutation was performed by that status check.
- Validation: login tests `5 passed`; Ruff clean on all changed code/tests; full suite `161 passed, 7 skipped`.

## Safety and decision constraints

- Never store access tokens, ARLs, app secrets, or private configuration in this file or tests.
- Do not download real media into the user's music library during tests; use temporary directories/local servers.
- Do not update the global `rip 2.1.0`, publish, push, merge, tag, or create a release without explicit authorization.
- Preserve existing Deezer and Qobuz behavior; changes require regression tests.
- Treat advertised quality as a hint. Selection must ultimately rely on the delivered manifest/file properties.

## Working tree expected at this handoff

The multi-source foundation is committed in `254c33c`, migration safety in `83a5f99`, opt-in best-source download in `16d01df`, TIDAL request/refresh safety in `90ac62a`, the 429 circuit breaker in `49fe134`, configuration-source correction in `8976012`, warning cleanup in `55ee197`, restored TIDAL device authentication in `541e9a3`, DASH initialization fix in `e76eaa5`, TIDAL single metadata correction in `97a2c2d`, and ISRC-first comparison in `3d0d832`. The explicit-login implementation, tests, and this accumulated memory update are modified but not yet committed.

Repository-local Git identity is configured as the existing project author. No global identity was changed and no push occurred.
