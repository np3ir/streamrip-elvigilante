# Streamrip ElVigilante — AI handoff context

Last updated: 2026-08-28 (America/La_Paz)

## Objective

Evolve `streamrip-elvigilante` 2.2.8 into a robust multi-source downloader:

1. Make its TIDAL authentication, quality cascade, download pipeline, and file construction as reliable and efficient as (or better than) sibling project `../tiddl-elvigilante`.
2. Preserve Deezer and Qobuz compatibility.
3. Match equivalent recordings across TIDAL, Qobuz, and Deezer.
4. Compare actual available audio properties and download the highest-fidelity candidate.

Default fidelity policy agreed during implementation: lossless stereo FLAC outranks lossy spatial/Atmos. Among lossless candidates, prefer bit depth, then sample rate, then bitrate. ISRC is the strong recording identity; title + artist + duration (3-second tolerance) is a conservative fallback when ISRC is absent.

Service routing policy agreed on 2026-08-28: TIDAL is primary, Deezer secondary, and Qobuz tertiary when normalized delivered quality is equal. Service priority never overrides objectively better delivered fidelity. Exact normalized ISRC remains the cross-service identity key.

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

Latest full run after adding collection comparison:

- `180 passed`
- `7 skipped` (credentials/integration tests unavailable)
- No test failures; the run logs a dependency deprecation and a pre-existing unclosed-session diagnostic during unrelated tests.
- Ruff clean on all modified/new files. A separate whole-repository Ruff run reports one pre-existing `RUF036` ordering issue in `streamrip/media/semaphore.py:10`; it is unrelated to the current comparison change.
- `git diff --check` clean except informational LF-to-CRLF warnings on Windows

Commands:

```powershell
& "$env:LOCALAPPDATA\streamrip-elvigilante-venv\Scripts\python.exe" -m pytest
& "$env:LOCALAPPDATA\streamrip-elvigilante-venv\Scripts\ruff.exe" check <changed files>
```

## Next work

1. Remove the two exact temporary live-validation directories after user confirmation or through an allowed safe cleanup mechanism; both paths are listed below.
2. Preserve one coherent reference album/playlist folder and collection metadata when `--download-best` selects tracks from mixed services; current collection download queues winning tracks individually through the normal single-track pipeline.
3. Improve TIDAL's stereo-lossless preference when an otherwise lossless release first returns EAC3 spatial audio.
4. Decide whether to prepare a side-by-side 2.2.8 installation without replacing global `rip 2.1.0`.

## Collection and direct-URL comparison

Implemented on 2026-08-28; pending local checkpoint commit at the time of this memory update.

- `rip compare` now accepts a standard TIDAL, Qobuz, or Deezer track, album, playlist, or artist URL directly. Existing `rip compare SOURCE TRACK_ID` syntax remains compatible.
- IDs for collections use `--type album`, `--type playlist`, or `--type artist`; `track` remains the default.
- Album and playlist tracks retain source order. Artist comparison resolves the service discography in batches of eight album-metadata requests, skips unavailable album responses, and de-duplicates repeated track IDs while preserving first occurrence.
- Preview resolution is metadata-only: it does not create collection folders or fetch artwork. Each recording is then compared using the existing exact-ISRC and delivered-quality pipeline.
- Collection output identifies every track, prints its service-quality table, and finishes with winner counts by service.
- `--download-best` queues the selected winner for every comparable track only after the complete preview. It remains explicitly opt-in. Collection-level folder/tag coherence across mixed winning services is still listed as follow-up work.
- Unit coverage was added for album, playlist, and artist resolution, including ordered de-duplication. Focused comparison/CLI validation: `19 passed`.
- Live preview used `https://tidal.com/album/545097792` (Bryan Adams, `Tough Town`, 10 tracks). All ten recordings matched Qobuz and Deezer exactly by ISRC. With the configured 16-bit/44.1-kHz ceiling, Qobuz and Deezer delivered FLAC 16/44.1; Qobuz won the deterministic tie on all ten. TIDAL delivered EAC3 spatial. The command completed with `Collection winners: qobuz: 10`; no media was downloaded.
- Full suite: `180 passed, 7 skipped`. Ruff is clean on all changed files. Whole-tree Ruff continues to report only the pre-existing `RUF036` in `streamrip/media/semaphore.py:10`; the dependency deprecation and unrelated unclosed-session diagnostic also remain non-failing.

## Explicit service tie-break priority

Implemented after collection comparison on 2026-08-28; pending local checkpoint commit at the time of this memory update.

- `choose_best()` now uses `TIDAL > Deezer > Qobuz` only after the full normalized quality rank is equal.
- Higher losslessness, bit depth, sample rate, bitrate, channel count, or spatial rank continues to win before service preference; this preserves the project's delivered-quality objective.
- Tests prove an exact-quality tie selects TIDAL, removal of TIDAL selects Deezer, and a higher-resolution Qobuz delivery still beats lower-resolution TIDAL.
- Focused multisource/comparison validation: `33 passed`; Ruff clean on changed code and tests.

### Configurable priority order

- Added permanent `[comparison].service_priority`, represented as an ordered TOML array. Default: `["tidal", "deezer", "qobuz"]`.
- Added repeatable CLI override `--priority SERVICE`; values are supplied from highest to lowest priority. Omitted services are appended using the permanent order, so partial overrides remain complete and deterministic.
- Configuration loading lowercases entries, removes duplicates/unknown services, and appends any missing supported services safely. Existing configurations without the new field load the default without requiring a schema-version migration.
- The user's active private config now explicitly stores TIDAL, Deezer, Qobuz order. Backup: `config.toml.before-service-priority.bak`; no credential values were printed or copied.
- Tests cover configuration persistence and a custom Deezer-first tie. Full suite: `183 passed, 7 skipped`; focused config/multisource/comparison/CLI tests: `50 passed`; Ruff clean on changed files.

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
- An attempted non-downloading `rip search` probe exposed a separate pre-existing CLI defect: `cli.py` called missing `Main.search_output_file()` and `Main.search_interactive()` methods. No product code was changed during credential validation; the defect was subsequently repaired in `73dbc30` as recorded below.

## Restored standalone search workflows

Committed as `73dbc30 fix: restore search workflows` (local only; not pushed).

- Restored `Main.search_interactive()`, `Main.search_take_first()`, and `Main.search_output_file()` against the current normalized `SearchResults` and asynchronous queue architecture.
- Interactive search queues only explicitly selected results. Output-file search writes UTF-8 JSON with unescaped Unicode in the importable `source`/`media_type`/`id`/`desc` format.
- Added `add_all_by_id()` and `_queue_by_id()` so multiple selections reuse one authenticated client per service rather than repeating login work.
- Empty-result searches now exit without creating a file or queueing media.
- Preview-only live searches completed successfully against Qobuz, Deezer, and TIDAL and produced parseable temporary JSON files; the exact temporary files were removed afterward. Qobuz and Deezer returned unrelated textual matches for the probe, while TIDAL returned the expected Scorpions recording. No selection or audio download occurred.
- Validation: focused search tests `5 passed`; full suite `168 passed, 7 skipped`; Ruff clean on the changed code/tests; `git diff --check` clean except informational Windows line-ending notices.

## Best-edition selection across services

Committed as `0b690b9 fix: select best matching service edition` (local only; not pushed).

- Each service now evaluates all de-duplicated matching editions returned by ISRC and metadata discovery, re-verifies identity after resolving the playable candidate, and retains the highest normalized audio quality for that service.
- A failed/unplayable matching edition no longer prevents later matching editions from being evaluated. If every candidate fails, the service error remains visible in the comparison report.
- The source used to start the comparison is also searched for alternate editions while retaining the already resolved reference as a safe seed.
- The comparison table now displays the selected service track IDs, making edition and reverse-reference checks auditable.
- Live preview-only validation used Daft Punk's `Get Lucky`. Starting from TIDAL `20115564` and inversely from Qobuz `9140031` converged on the same exact-ISRC set: Qobuz `9140031` at FLAC/lossless/24-bit/88.2 kHz, TIDAL `20115564` as AAC (`MP4A.40.2`) for that edition, and Deezer `67238735` at FLAC/lossless/16-bit/44.1 kHz. Qobuz was selected in both directions. No audio was downloaded.
- A superficially similar Qobuz result, `8767428`, correctly resolved to a different exact-ISRC set (TIDAL `19823990`, Deezer `66609426`) at up to 24-bit/44.1 kHz; this explained the earlier apparent inconsistency and confirms that matching is recording-specific rather than title-only.
- All temporary JSON catalog-probe files and their exact temporary directory were removed after validation.
- Validation: comparison/CLI tests `15 passed`; full suite `170 passed, 7 skipped`; Ruff clean on changed code/tests; `git diff --check` clean except informational Windows line-ending notices. The suite still logs a dependency deprecation and a pre-existing unclosed-session diagnostic during unrelated tests; neither failed the suite.

## Controlled best-source download validation

- With explicit user authorization, the isolated Streamrip 2.2.8 executable ran `compare tidal 20115564 --download-best`. The global `rip 2.1.0` installation was not invoked or modified.
- The comparator again matched TIDAL `20115564`, Qobuz `9140031`, and Deezer `67238735` by exact ISRC and selected Qobuz at FLAC/lossless/24-bit/88.2 kHz.
- The normal download pipeline completed successfully and created `C:\Users\DJ Elvigilante\Music\Daft Punk\(2023-11-17) Random Access Memories\Daft Punk, Pharrell Williams - Get Lucky (feat. Nile Rodgers).flac` (133,542,790 bytes).
- Independent `ffprobe` inspection confirmed a FLAC audio stream, 24-bit raw depth, 88,200 Hz, stereo, duration 369.614558 seconds, plus an embedded MJPEG cover stream.
- Tags include title, artist, album, copyright, track 8, disc 1, year 2023, and ISRC `USQX91300108`. SHA-256: `27464EACA9B9B2084404205B0457149D0D686A5E7955BEB9831A279B35BB97CF`.
- This was the first authorized real-media download for the cross-service selector. The file remains in the configured music library; it was not removed because it is the requested output.

## TIDAL lossless-delivery diagnosis for album 111808317

- Metadata-only inspection identified the album as Marta Soto's `Míranos (Deluxe Edition)`, 28 tracks, released 2018-08-31. Every track has an ISRC and preview-only comparison found an exact match in TIDAL, Qobuz, and Deezer.
- Qobuz and Deezer delivered FLAC/lossless/16-bit/44.1 kHz for the inspected album tracks. TIDAL metadata advertised `audioQuality=LOSSLESS` and the `LOSSLESS` media tag, but playback requests for both `HI_RES_LOSSLESS` and `HI_RES` returned `HIGH` AAC (`mp4a.40.2`).
- The explicit `LOSSLESS` probe was stopped by the existing account-protection circuit breaker after repeated HTTP 429 responses; no attempt was made to bypass the guard and no album audio was downloaded.
- Local tiddl-elvigilante inspection explains the discrepancy: it uses hybrid TIDAL authentication with a HiRes-capable session for MAX/24-bit and a separate TV-client session for reliable LOSSLESS/16-bit fallback. Its downloader explicitly re-requests `LOSSLESS` through the TV session when the HiRes session degrades a lossless-only track to `HIGH` AAC.
- Streamrip currently stores and uses only one TIDAL session/client identity. Reproducing tiddl-elvigilante's dual-session routing is therefore the next required TIDAL fix; merely changing the configured quality tier cannot solve this case.

## Hybrid TIDAL sessions and user-selected quality ceilings

Committed as `db3fbfd feat: add hybrid tidal and quality ceilings` (local only; not pushed).

- Introduces a second private token store and independent TV-client OAuth/session state for reliable TIDAL LOSSLESS fallback while preserving the existing HiRes token as primary.
- Adds lazy fallback routing: when the primary HiRes session returns lossy audio for a requested lossless tier, the TV session requests LOSSLESS and the higher normalized delivery wins.
- Adds `rip login tidal --fallback` device authorization for the second token, shared request-budget plumbing, explicit closing of both sessions, and an initial regression test for HiRes-to-TV fallback.
- `rip compare` now accepts `--max-bit-depth` and `--max-sample-rate` as service-neutral strict ceilings. Example: requesting 16-bit searches TIDAL, Qobuz, and Deezer for the best matching delivery at or below 16-bit and never selects 24-bit merely because it normally ranks higher.
- If the requested ceiling is unavailable, selection must cascade downward to the next-best actually delivered quality across all authenticated services.
- Service marketing tiers are inputs only. The ceiling and fallback decision must use normalized delivered properties (lossless flag, bit depth, sample rate, bitrate, channels/spatial) and retain exact-ISRC/identity safeguards.
- Lossless deliveries with unknown properties are excluded when their corresponding ceiling cannot be proven; lossy audio remains the final lower-quality fallback. For a 16-bit ceiling, service requests are proactively limited to their CD-quality tier where supported.
- Live preview-only validation `compare --max-bit-depth 16 tidal 20115564` changed Qobuz from its normal 24-bit/88.2 kHz delivery to FLAC 16-bit/44.1 kHz and selected it over TIDAL AAC, tied technically with Deezer FLAC 16-bit/44.1 kHz. No audio was downloaded.
- Validation: focused tests `52 passed`; full suite `175 passed, 7 skipped`; Ruff clean on all changed code/tests. The pre-existing dependency deprecation and unrelated unclosed-session diagnostic remain visible in the suite log.
- Live fallback validation completed after the user authorized the second TIDAL device session. The private token was written only to its dedicated local fallback store and is absent from this repository and memory.
- Preview-only `compare --max-bit-depth 16 tidal 111808318` then delivered TIDAL FLAC/lossless/16-bit/44.1 kHz instead of the previous AAC result. Qobuz and Deezer also delivered FLAC 16-bit/44.1 kHz; Qobuz won the deterministic tie because it explicitly reported two channels while the TIDAL manifest left channel count unknown. No audio was downloaded.

## Persistent comparison-quality policy

Committed as `9bbdb8a feat: persist comparison quality policy` (local only; not pushed).

- Added a backward-compatible `[comparison]` configuration section with `max_bit_depth`, `max_sample_rate` (kHz), `prefer_lossless`, and `fallback_to_lossy`. Existing same-version configurations without the section load safe defaults and acquire the section on a controlled save.
- `rip compare` now reads the permanent policy whenever the corresponding CLI option is omitted. CLI flags remain one-run overrides, including `--prefer-lossless/--no-prefer-lossless` and `--fallback-to-lossy/--no-fallback-to-lossy`.
- When lossy fallback is disabled and no lossless candidate satisfies the ceiling, no winner is selected or downloaded. Unknown lossless technical properties remain ineligible when they cannot prove compliance with an active ceiling.
- The user's active private config was backed up to `config.toml.before-comparison-policy.bak`, then configured for maximum 16-bit/44.1 kHz, lossless preference enabled, and lossy fallback enabled. No credential fields were read, printed, or copied into the repository.
- Live preview `rip compare tidal 20115564` without quality flags proved the permanent policy was applied: TIDAL, Qobuz, and Deezer all resolved to FLAC 16-bit/44.1 kHz and Qobuz won the deterministic tie. No audio was downloaded.
- Validation: focused configuration/comparison tests `46 passed`; full suite `177 passed, 7 skipped`; Ruff clean on changed files. A zero-byte stale Git `packed-refs.lock` left during commit was removed only after confirming no Git process was active.

## Explicit service-login foundation

Committed as `72f1df0 feat: add secure service login commands` with documentation checkpoint `24d91d3 docs: record service login foundation` (local only; not pushed).

- New `rip login` command group with `qobuz`, `deezer`, `status`, and `logout` commands.
- Qobuz supports hidden email/password input or `--token` user-ID/token input. After a successful email/password exchange, only the returned user ID and `user_auth_token` are persisted; neither the clear password nor its MD5 is written to disk.
- Qobuz app ID and reusable app-secret candidates remain automatically discovered through the existing web-player extraction path. Manual token login remains available, matching QobuzDownloaderX's two login modes.
- Removed Qobuz credential values and full login responses from debug logs and authentication exceptions.
- Deezer manual ARL entry is hidden and validated before replacing the credential stored on disk. Manual ARL remains a supported fallback.
- `rip login status` reports only whether TIDAL/Qobuz/Deezer are configured. `rip login logout SERVICE` removes the selected user credential while preserving reusable Qobuz app metadata.
- Real command-object validation shows all three current services as configured without printing credential values. No login, logout, download, or user-config mutation was performed by that status check.
- Validation: login tests `5 passed`; Ruff clean on all changed code/tests; full suite `161 passed, 7 skipped`.

## Deezer browser-assisted login

Committed as `2fda1e0 feat: add assisted deezer browser login`, with documentation checkpoints `65ee91a` and `573a888` (local only; not pushed).

- `rip login deezer` now defaults to an isolated Deezer login window backed by WebView2. After the user completes Deezer's own login, Streamrip reads only the resulting `arl` cookie, closes the window, validates it through the existing client, and persists it only after successful validation.
- `rip login deezer --arl` retains hidden manual entry as an explicit fallback.
- pywebview is a Windows-only optional dependency exposed through the `browser-login` Poetry extra, so headless and non-Windows installations do not acquire GUI dependencies.
- The browser uses private mode, so its temporary profile is discarded. No email/password fields are implemented or intercepted by Streamrip.
- Cookie extraction covers pywebview's mapping, attribute, and `SimpleCookie` representations. Closing the window before authentication produces a controlled cancellation error.
- pywebview 6.2.1 was installed only in the isolated development environment. A real local WebView2 engine probe opened and automatically closed a private test page successfully; it did not visit Deezer or alter any service session.
- User-observed live validation then succeeded end to end: `rip login deezer` opened Deezer, the user logged in directly with the Deezer password in the private window, Streamrip captured the cookie without displaying it, closed the window automatically, validated the account, and saved the replacement session. Google federated login was rejected inside the embedded browser, but direct Deezer password login succeeded. No ARL, email, Google credential, or password is recorded here.
- A follow-up preview-only `rip compare tidal 524417109` succeeded with the newly captured Deezer session and no credential error or media download. Deezer and Qobuz still had no equivalent catalog match; TIDAL remained the sole candidate and winner at FLAC/lossless/24-bit/44.1 kHz.
- Installing Poetry temporarily upgraded `tomlkit`; the development environment was immediately restored to the project-compatible `tomlkit 0.7.2` before tests. Poetry remains installed as a development tool but is not a product dependency.
- Validation: login tests `7 passed`; Ruff clean on changed code/tests; full suite `163 passed, 7 skipped`; `git diff --check` clean except Windows line-ending notices.

## Safety and decision constraints

- Never store access tokens, ARLs, app secrets, or private configuration in this file or tests.
- Do not download real media into the user's music library during tests; use temporary directories/local servers.
- Do not update the global `rip 2.1.0`, publish, push, merge, tag, or create a release without explicit authorization.
- Preserve existing Deezer and Qobuz behavior; changes require regression tests.
- Treat advertised quality as a hint. Selection must ultimately rely on the delivered manifest/file properties.

## Resumable mass-library planner

Implemented on 2026-08-28 after auditing `../tiddl-elvigilante` link-processing behavior. Committed locally as `4d6c6c7 feat: add resumable multisource library planner`; no push occurred.

- New `rip library URL` command accepts standard TIDAL, Qobuz, or Deezer track, album, playlist, artist, and mix links.
- Playlist expansion modes mirror tiddl-elvigilante: `--tracks`, `--albums`, and `--artists`. Track mode deduplicates by normalized ISRC. Album/artist modes retain album-specific track keys so complete albums are not made incomplete by cross-album recording deduplication.
- `--dry-run` is the safe default; actual transfer requires explicit `--download`. `--max-tracks N` limits attempted new tracks, including failures. `--resume` skips successful keys from the identical job before comparison.
- Checkpoints live privately under Streamrip's app-data `library-resume` directory. Their signature includes URL, expansion mode, preview/download mode, quality ceilings, lossless/fallback policy, and service priority. Writes use a same-directory temporary followed by atomic replace. Checkpoints contain only job signature and completed track keys, never credentials.
- Expansion is streamed rather than pre-creating thousands of asyncio tasks. Playlist albums, credited artists, artist albums, and shared albums are de-duplicated before repeated catalog requests where possible.
- Each yielded recording reuses the existing exact-ISRC multi-source comparator, delivered-quality ceiling, and configurable service tie-break. CLI output is one concise line per recording plus aggregate winner/failure/duplicate/resume counts rather than a full Rich table per service.
- Download execution queues selected winning-service tracks through Streamrip's existing pipeline after planning, with canonical reference-album metadata overrides so mixed-service winners are filed and tagged using the requested library edition rather than each winning service's edition.
- Real preview validation against TIDAL playlist `cd353f9c-d621-44b9-aa6e-a0497541d908` used `--max-tracks 1` only. Track mode selected Deezer FLAC 16/44.1 for the first recording; a second identical `--resume` run skipped it and selected Qobuz FLAC 16/44.1 for the next recording. Album mode successfully expanded and selected Deezer FLAC 16/44.1 for its first track. Artist mode expanded the first credited artist discography and selected Deezer FLAC 16/44.1 for its first track. No media was downloaded.
- The earlier metadata-only inventory measured 2,488 playlist entries, all with ISRC, 2,066 referenced albums, 1,110 primary artists, and 41 duplicate TIDAL track IDs.
- Validation: focused library/CLI tests `12 passed` after the shared-album regression; full suite `191 passed, 7 skipped`; Ruff clean on changed files; `git diff --check` clean except informational Windows line-ending notices.

## Working tree expected at this handoff

The multi-source foundation is committed in `254c33c`, migration safety in `83a5f99`, opt-in best-source download in `16d01df`, TIDAL request/refresh safety in `90ac62a`, the 429 circuit breaker in `49fe134`, configuration-source correction in `8976012`, warning cleanup in `55ee197`, restored TIDAL device authentication in `541e9a3`, DASH initialization fix in `e76eaa5`, TIDAL single metadata correction in `97a2c2d`, ISRC-first comparison in `3d0d832`, explicit service-login commands in `72f1df0`, browser-assisted Deezer login in `2fda1e0`, restored standalone search in `73dbc30`, best-edition selection in `0b690b9`, hybrid TIDAL plus quality ceilings in `db3fbfd`, persistent comparison policy in `9bbdb8a`, collection/direct-URL comparison in `74fd70c`, configurable service priority in `6b8217e`, and the resumable mass-library planner in `4d6c6c7`. No push occurred.

## Canonical library metadata with best-source audio

Implemented on 2026-08-29 as the next phase of the mass-library planner.

- Download-mode library jobs now keep the expanded source track as the canonical reference while obtaining the audio stream from the cross-service quality winner. With the recommended TIDAL-first setup, paths, tags, album edition, cover, and lyrics therefore remain TIDAL-derived even when Deezer or Qobuz supplies better audio.
- Canonical album metadata is fetched from the reference album endpoint so album totals, disc layout, dates, artwork, and folder formatting are not taken from a possibly different winning-service edition. A summarized reference-track response is retained as a safe fallback if that album request is unavailable.
- Source subdirectories identify the canonical library source, not the audio supplier. Multi-disc albums retain the configured disc-subdirectory behavior, while shared cover art is stored at the album root.
- Resume checkpoints advance only after successful post-processing or when an exact-path/ISRC-existing file is verified. Failed transfers stay pending for the next `--resume` run.
- Failure bookkeeping uses the actual audio supplier and its track ID, while successful library identity and deduplication remain tied to canonical metadata and ISRC.
- Changed product files: `streamrip/library.py`, `streamrip/rip/main.py`, `streamrip/rip/cli.py`, and `streamrip/media/track.py`. Regression coverage is in `tests/test_library.py`.
- Validation: focused library tests `10 passed`; full suite `194 passed, 7 skipped`; Ruff clean on all changed code/tests; `git diff --check` reported only informational Windows line-ending notices.
- No real audio was downloaded during this phase. A small explicitly authorized live download remains desirable later to verify final directory, embedded tags, cover, lyrics, checkpoint advancement, and mixed-source failure recovery end to end.

### Authorized live library validation

- On 2026-08-29 the user explicitly authorized one real transfer from the massive TIDAL playlist. The isolated 2.2.8 development executable ran `rip library <playlist> --tracks --download --max-tracks 1`; global `rip 2.1.0` was not invoked or modified.
- The canonical TIDAL recording was Gepe / Mon Laferte `BOLERo LIBRA`, exact ISRC `ES71G2420467`. Deezer won delivery at FLAC/lossless/16-bit/44.1 kHz under the permanent ceiling and service-priority policy. The command reported `processed=1`, `attempted=1`, `failed=0`, and no duplicates or resume skips.
- The resulting canonical path is `C:\Users\DJ Elvigilante\Music\Gepe\(2024-09-26) BOLERo LIBRA\Gepe, Mon Laferte - BOLERo LIBRA.flac` (18,157,098 bytes). Mutagen independently confirmed FLAC, 16-bit, 44,100 Hz, stereo, duration 172.145 seconds, one embedded picture, track/disc 1, title, artists, album artist, album, year, and the expected ISRC. SHA-256: `61EDEB5999D1DA1CC043F48638A5D02AF59B0ACE0DAE7DCAB2F3684E120835AE`.
- The private checkpoint `d71162429ed3bc1b9cd7.json` contains the job signature and completed key `isrc:ES71G2420467`, proving completion advanced only after successful processing. It contains no credential or track metadata. The command was not rerun with `--resume`, because doing so would intentionally advance to and download a second track.
- No standalone `.lrc` was produced for this delivery; cover presence was verified through the embedded FLAC picture. Future controlled validation should exercise a forced transfer failure and recovery without downloading additional successful media unnecessarily.

## Accurate library failure accounting

- The post-validation audit found that `rip library` could print `failed=0` when a queued worker later failed during metadata resolution, transfer, or post-processing. `Main.worker_loop` caught those failures internally, while the CLI counter covered only comparison/planning failures.
- A failure callback now flows through `PendingLibraryTrack`, `Track`, `Main.add_library_track`, and the library CLI. Final transfer exhaustion, a `None` resolution, a resolution exception, and a later media-processing exception each increment the library failure summary exactly once without invoking the completion callback or advancing the resume checkpoint.
- Failure database identity remains the actual audio service and winning-service track ID. Successful canonical identity remains the reference track and ISRC.
- Changed product files: `streamrip/library.py`, `streamrip/media/track.py`, `streamrip/rip/main.py`, and `streamrip/rip/cli.py`; regression coverage is in `tests/test_library.py`.
- Validation: focused library tests `13 passed`; full suite `197 passed, 7 skipped`; Ruff clean on all changed code/tests; `git diff --check` reported only informational Windows line-ending notices.
- No media was downloaded and no credentials, global installation, push, or release were touched during this audit.

## Reference metadata request reuse

- The next efficiency audit found one avoidable reference-service metadata request per library track: collection expansion already supplied the catalog track response, but comparison called `get_candidate`, which fetched the same track metadata again before resolving its audio manifest.
- `LibraryTrack` now carries that already-fetched response only for the lifetime of the streamed planning item. The library comparator combines it with a newly resolved downloadable to build the reference candidate, eliminating the duplicate metadata call while preserving delivered-quality inspection and same-service best-edition searches.
- Final file construction still requests canonical track and full-album metadata independently. This optimization therefore does not trade away tag, edition, cover, lyrics, disc-layout, or path quality.
- Raw reference metadata is explicitly omitted from `track_asdict`, preventing future manifests/checkpoints from becoming large or retaining unnecessary service payloads.
- Changed files: `streamrip/library.py`, `streamrip/rip/cli.py`, and `tests/test_library.py`. Validation: focused library/CLI tests `18 passed`; full suite `197 passed, 7 skipped`; Ruff clean; `git diff --check` reported only informational Windows line-ending notices.
- No media was downloaded and no private configuration, credentials, global installation, push, or release were touched.

## Bounded comparison concurrency and audit manifests

- Library planning now supports `--workers 1..8`; the permanent `[comparison].library_workers` default is conservatively set to 2. A bounded async mapper caps in-flight comparison tasks, yields results in original source order, and cancels/gathers remaining tasks cleanly on interruption.
- The concurrency value intentionally does not alter the job signature because it changes execution performance, not selection semantics. Existing per-client rate limiting and TIDAL request/circuit-breaker protections remain authoritative.
- Credential-free JSONL manifests are enabled by `[comparison].library_manifest = true`. The default private path is Streamrip app data under `library-manifests/<signature>.jsonl`; `--manifest-path` overrides it and `--no-manifest` disables it for one run.
- Manifest events cover planning failure, no match, preview selection, download selection, completion, and download/processing failure. Curated fields include canonical identity, ISRC, winner identity, normalized delivered quality, status, timestamp, and final path when available. Raw service responses, credentials, tokens, ARLs, passwords, and exception messages are never written.
- Each JSONL line is flushed and fsynced for crash durability. Manifest I/O failures warn but do not turn valid downloads into failures. Completion callbacks now receive the verified final path; worker-level failures receive an empty path and do not advance checkpoints.
- CLI documentation now explains concurrency, manifests, and permanent configuration. A stale planner note claiming canonical metadata was future work was corrected.
- The active private config was backed up as `config.toml.before-library-audit.bak`, then explicitly set to `library_workers = 2` and `library_manifest = true`. Only the non-secret comparison section was inspected; no credential value was printed or copied.
- Changed files: `streamrip/config.py`, `streamrip/config.toml`, `streamrip/library.py`, `streamrip/media/track.py`, `streamrip/rip/cli.py`, `streamrip/rip/main.py`, `README.md`, `tests/test_compare_cli.py`, `tests/test_config.py`, and `tests/test_library.py`.
- Validation before the final documentation update: focused tests `32 passed`; full suite `199 passed, 7 skipped`; Ruff clean; `git diff --check` reported only informational Windows line-ending notices. No real service request or media download was performed.
- Next controlled live test requires explicit notice to the user first: preview a very small bounded job to verify actual request concurrency/order and inspect its manifest, then separately authorize any download-mode validation.

### Authorized live concurrent preview

- On 2026-08-30 the user authorized a metadata/manifest-only live test against TIDAL playlist `cd353f9c-d621-44b9-aa6e-a0497541d908`: track expansion, dry-run, three attempted tracks, two comparison workers, and an isolated manifest path. No audio was downloaded and global `rip 2.1.0` was not invoked.
- The command completed in source order 1–3 with `processed=3`, `attempted=3`, `failed=0`, no duplicates, no resume skips, and no HTTP 429/circuit-breaker event. Deezer won tracks 1 and 3 at delivered FLAC/lossless/16-bit/44.1 kHz. TIDAL was the only eligible winner for track 2 and delivered lossy `mp4a.40.2`, correctly accepted by the configured lossy fallback policy.
- Qobuz login was rejected for comparison with `IneligibleError: Free accounts are not eligible to download tracks`; therefore this run effectively compared TIDAL and Deezer. Restoring an eligible Qobuz subscription/session is required for a true three-service live validation.
- The isolated JSONL manifest contains exactly three `previewed` events in source order. Each records canonical TIDAL ID/ISRC, selected audio service/ID, codec, lossless state, bit depth, and sample rate. A structural scan found no token, password, secret, ARL, credential, cookie, or email key.
- Checkpoint signature `292f7b1b6334af8c6b48` contains exactly the three completed ISRC keys and no track metadata or credentials. The isolated manifest is stored privately as `live-audit-3-tracks.jsonl` in the Streamrip app-data `library-manifests` directory.
- TIDAL primary/fallback session refreshes completed normally. The next real download test must be separately authorized and should remain limited to one track; a three-service test should wait until Qobuz eligibility is restored unless the purpose is explicitly to verify two-service fallback.

### Authorized one-track concurrent download validation

- The user then explicitly authorized one real download. On 2026-08-30 the isolated 2.2.8 executable ran the same playlist in track/download/resume mode with `--max-tracks 1`, two comparison workers, and an isolated manifest. It skipped the one previously completed download key and attempted exactly the next recording; global `rip 2.1.0` was not invoked or modified.
- TIDAL's hybrid cascade delivered Kola Loka `No Me Da Mi Gana Americana`, ISRC `GBBGT0800160`, as FLAC/lossless/16-bit/44.1 kHz. This is materially better than the AAC result observed for the same track during the prior preview and confirms that selection/audit must retain actually delivered properties for each run rather than treating preview marketing/session results as permanent.
- The file is `C:\Users\DJ Elvigilante\Music\Kola Loka\(2008-06-01) No Me Da Mi Gana Americana\Kola Loka - No Me Da Mi Gana Americana.flac` (24,913,465 bytes). Mutagen independently confirmed FLAC, 16-bit, 44,100 Hz, stereo, exactly 273 seconds, one embedded picture, canonical title/artist/album/album artist/year/track/disc tags, and the expected ISRC. SHA-256: `A4DF65754E72F4AD9CE2EE1A760195FAB5CF811A6EF108018CCD029550829B56`.
- The isolated manifest contains exactly one `selected` and one `completed` event with matching identities and quality; the latter contains the verified final path. The download checkpoint now contains exactly the earlier Gepe ISRC plus `GBBGT0800160`. Summary: processed 1, attempted 1, TIDAL winner 1, failed 0, resume-skipped 1. Qobuz remained unavailable due account eligibility.
- No standalone LRC or separate cover file was created; the cover is embedded as configured. The test exposed mixed Windows path separators in the manifest's otherwise valid path, so canonical album folders are now normalized with `os.path.normpath` before track path construction.
- Path-normalization validation: focused library tests `15 passed`; full suite `199 passed, 7 skipped`; Ruff clean; `git diff --check` reported only informational Windows line-ending notices. No additional media was downloaded during automated tests.

## tiddl path-construction parity audit and phase 1

- The user requested that Streamrip emulate or exceed sibling `../tiddl-elvigilante` path and file construction. Its repository instructions were read before inspection. The audit covered `tiddl/core/utils/format.py`, `strings.py`, download templates/config, downloader skip logic, safe publish helpers, placeholder documentation, and destination-safety design.
- Existing Streamrip ports already matched several tiddl behaviors: Unicode NFC and dash normalization, full-width forbidden-character substitutions, Zalgo cleanup, primary/featured artist separation and normalized deduplication, a three-artist filename cap with `& others`, explicit/version placeholders, explicit album-title cleanup, automatic `Disc N` folders, release-date preservation, cover embedding, and LRC sidecars.
- A verified divergence affected library paths: `PendingLibraryTrack._canonical_folder` truncated the entire rendered relative hierarchy to 150 Python characters. This could cut an album name or separator and did not match tiddl, which preserves hierarchy and constrains each filesystem component independently by UTF-8 bytes.
- `clean_filepath` now splits the hierarchy first, sanitizes each component independently, preserves drive/UNC/absolute prefixes, normalizes Unicode and Windows-reserved names, and caps every component at 255 UTF-8 bytes. `clean_filename` accepts an explicit byte ceiling while retaining its conservative 240-byte default for media filenames and `.part` suffix safety.
- The default total-path limit changed from 240 bytes to 4000 bytes, matching tiddl's PATH_MAX-oriented policy. This prevents long but valid directory hierarchies from crushing or corrupting the filename; component limits remain the primary filesystem guarantee. Callers can still request an explicit smaller total limit.
- Canonical library folders no longer apply the destructive 150-character slice and now retain the complete sanitized template hierarchy. User-selected `folder_format` and `track_format` were deliberately preserved; this phase ports mechanics without silently changing the user's organizational naming policy.
- New regression coverage verifies hierarchy preservation, UTF-8 component limits, NFC, Windows reserved names, extension-preserving explicit truncation, and canonical folders over 150 characters. Validation: focused path/library tests `20 passed`; full suite `204 passed, 7 skipped`; Ruff clean; `git diff --check` reported only informational line-ending notices.
- Remaining high-value parity gap: tiddl uniformly stages, verifies, fsyncs, and atomically publishes media while retaining a verified local copy on destination failure. Streamrip uses `.part` publication for some downloadables but lacks one cross-service verification/publish contract and destination-volume identity protection. This is the next audited implementation phase; no real media download is required for its offline tests.

### Cross-service staged and atomic media publication

- The downloader audit confirmed inconsistent prior behavior: Qobuz/basic HTTP and single-URL TIDAL wrote directly to the final filename, while Deezer and segmented TIDAL used provider-specific `.part` files. A process/network failure could therefore expose a partial final file for some services but not others.
- Every `Downloadable` now writes first to a collision-resistant local staging file created with `tempfile.mkstemp`, independent of TIDAL/Deezer/Qobuz. Missing or zero-byte staging output is rejected before publication.
- `streamrip/file_publish.py` provides the common publication contract. It flushes/fsyncs the verified local stage. On the same volume it atomically replaces the final path. Across volumes it copies to a destination-side unique temporary file, flushes it, verifies exact byte count and SHA-256 against the source, and only then atomically replaces the final path.
- A failed transfer deletes invalid local partials but never touches an older valid final file. A failed destination publish removes its destination-side temporary, preserves the verified local stage, raises `PublishError` with the retained path, records the winning service failure, and leaves the library checkpoint pending.
- Publish failures are not treated like network failures: Streamrip does not re-download already valid bytes through the ordinary exponential retry loop. Successful cross-volume publication removes local staging best-effort; a cleanup failure does not falsely report publication failure.
- Offline tests cover same-volume atomic replacement, invalid-transfer cleanup with prior-final preservation, forced cross-volume SHA-256 publication, corruption refusal with retained source/prior final, and no redownload/checkpoint advancement after `PublishError`.
- Changed files: new `streamrip/file_publish.py`, `streamrip/client/downloadable.py`, `streamrip/media/track.py`, new `tests/test_file_publish.py`, and `tests/test_library.py`. Validation: focused publication/library/retry tests `27 passed`; full suite `209 passed, 7 skipped`; Ruff clean; `git diff --check` reported only informational line-ending notices. No real service request or media download was performed.
- Residual gap versus tiddl after this publication phase was a durable recovery registry/CLI and trusted destination-volume identity. The recovery registry is now implemented as described below; destination-volume identity remains pending before claiming full NAS/USB safety parity.

### Durable staging recovery registry and CLI

- Failed final publication now registers each verified retained staging file under the private Streamrip app-data `publish-recovery` directory. Each recovery is an independent, atomically replaced JSON record, avoiding a shared mutable index during concurrent library workers.
- Records contain only recovery version, opaque ID, UTC creation time, absolute staging/destination paths, byte count, and SHA-256. No URL, service credential, token, ARL, email, title metadata, or account response is stored.
- `rip recovery list` reports pending IDs, byte counts, and destinations. `rip recovery retry ID` revalidates the retained file's exact size and SHA-256, uses the existing verified/atomic publication path without contacting a service or downloading again, and deletes the record only after success. `rip recovery discard ID` requires confirmation unless `--yes` is supplied and refuses to delete a staging file whose content no longer matches its record.
- Publication errors now include the recovery ID. If the registry itself is unavailable, the original `PublishError` remains truthful, retains and reports the staging path, and adds the registry failure instead of masking the publication failure.
- Changed files: `streamrip/file_publish.py`, `streamrip/client/downloadable.py`, `streamrip/rip/cli.py`, and `tests/test_file_publish.py`. Focused publication/library tests: `26 passed`. The four full-suite search failures were independently confirmed as Rich live-display state leakage from suite ordering: `tests/test_search_main.py` passes alone (`5 passed`). Total full-suite result before isolation was `210 passed, 7 skipped, 4 failed`; all changed-file Ruff checks pass and `git diff --check` reports only informational Windows line-ending notices. No service request, credential operation, or media download was performed.
- Remaining safety work: bind queued destinations to a trusted volume identity so retry cannot silently publish to a different disk mounted at the same drive letter or path. After that, add simulated volume disappearance/reappearance tests before any optional real removable-drive validation.

### Trusted destination-volume identity

- Streamrip now implements the same two-sided trust model audited in sibling `tiddl-elvigilante`: an exclusive `.streamrip-anchor` marker on the destination volume plus an independent per-machine record under private app data. Neither downloads nor recovery can create, replace, rotate, or adopt a marker automatically.
- New CLI commands are `rip destination trust PATH`, `status PATH`, and `forget PATH`. Trust requires interactive confirmation unless `--yes` is explicit; an existing marker is never overwritten and requires deliberate `--adopt-existing`. Forget removes only this machine's local record and preserves the shared marker.
- `[downloads].destination_identity` accepts `off` (safe compatibility default) or `strict`. Existing configs are backfilled to `off`; invalid values also fall back to `off` with a warning. Strict mode fails closed when no configured root contains the output, local trust is missing/invalid, the marker is absent/unreadable/invalid, containment fails, or IDs disagree.
- Strict checks run before album, playlist, single-track, mass-library, and video directory creation; before each audio/video transfer; immediately before final publication; and before tag/conversion/LRC post-processing. If identity changes after valid bytes were downloaded, publication is refused and the verified stage is registered with the original destination root and anchor ID.
- Recovery records remain backward-compatible. New strict-mode records bind destination root and anchor ID; `rip recovery retry ID` verifies both the retained file hash and currently mounted marker before writing. Legacy/unbound records are refused in strict mode instead of guessed.
- New `streamrip/destination_identity.py` uses lexical normalized containment without network-resolving paths, rejects marker symlinks and files over 4096 bytes, validates versioned JSON, writes local records atomically, and uses exclusive marker creation to close replacement races.
- Changed files: `streamrip/destination_identity.py`, `streamrip/config.py`, `streamrip/config.toml`, `streamrip/file_publish.py`, `streamrip/client/downloadable.py`, `streamrip/library.py`, `streamrip/media/{album,playlist,track,video}.py`, `streamrip/rip/cli.py`, `tests/test_destination_identity.py`, and `tests/test_file_publish.py`.
- Validation: destination/publication/library/retry tests `38 passed`; all tests except the known order-sensitive Rich search module `215 passed, 7 skipped`; that module independently passes `5 passed`. Ruff passes for every changed production/test file and `git diff --check` reports only informational Windows line-ending notices. All identity tests use temporary simulated volumes. No real marker, private config activation, service request, credential operation, or media download occurred.
- Operational next step requires the user to identify and confirm the intended real download root while it is mounted. Then run the isolated 2.2.8 client to trust it, verify `destination status`, and only afterward set `destination_identity = "strict"`. Do not enable strict mode first or downloads will correctly be refused.

### Real destination trust activation

- On 2026-09-05 the user identified `Z:\` as the intended library root. Read-only inspection confirmed it was mounted as the NTFS network drive labeled `SERVER`, backed by the existing `\\Servidor\server\Music` share, and that no Streamrip marker existed before authorization.
- The isolated Streamrip 2.2.8 client established the destination marker and per-machine trust record, then `rip destination status Z:\` confirmed the two records matched. The opaque anchor ID is intentionally not copied into this repository memory.
- The private config was backed up as `config.toml.before-destination-identity.bak`, then changed from the prior local Music folder to `folder = "Z:/"` and `destination_identity = "strict"`. No credential fields were read, printed, copied, or changed.
- A read-only guard probe for a hypothetical child path under `Z:\` returned trusted; a second CLI status check passed and `rip recovery list` reported no retained staging files. No probe directory/file was created, no service was contacted, and no media was downloaded.
- Future writes will now fail closed if `Z:\` is unavailable, its `.streamrip-anchor` is absent/invalid, or a different volume/share appears at the same path. Do not delete or edit the marker manually; use `rip destination status`, `trust --adopt-existing`, or `forget` as appropriate.

Repository-local Git identity is configured as the existing project author. No global identity was changed and no push occurred.

## Physical TIDAL quality verification

- An explicitly authorized one-track validation of TIDAL track `524417109` exposed a correctness defect: comparison advertised the candidate as FLAC/lossless/16-bit/44.1 kHz, while independent inspection of the completed file found FLAC/lossless/24-bit/44.1 kHz. The file remains under the trusted `Z:\` library; it was not deleted or altered. No further audio download was performed.
- The cause was reliance on TIDAL playback-response `bitDepth`. The new bounded stream probe reads at most 64 KiB from the signed media URL and decodes the physical FLAC STREAMINFO bit depth, sample rate, and channel count before accepting a lossless candidate. Native FLAC and an embedded FLAC marker in initialization data are supported.
- For the requested 16-bit TIDAL tier, a measured stream above 16 bits is rejected and the existing tier cascade continues. The check fails closed: if the physical header cannot be measured, the claimed 16-bit lossless response is not accepted blindly and the cascade continues to the next playable lower tier. Higher requested TIDAL tiers retain their existing behavior while using measured properties when available.
- A subsequent live comparison-only check of the same track performed no download and reported physically inspected FLAC/lossless/16-bit/44.1 kHz/stereo. `Z:\` destination identity passed before the check; Qobuz remained ineligible. The opaque destination anchor ID is not recorded here.
- Changed files: new `streamrip/client/audio_probe.py`, `streamrip/client/tidal.py`, new `tests/test_audio_probe.py`, and `tests/test_tidal_quality.py`.
- Validation: focused probe/TIDAL/manifest/comparison tests `27 passed`; full suite excluding the known order-sensitive Rich search module `219 passed, 7 skipped`; that module independently passed `5 passed`; Ruff clean and `git diff --check` reported only informational Windows line-ending notices.
- Final publication now has the corresponding defense-in-depth guard. A TIDAL downloadable selected from the 16-bit tier carries that physical limit into the shared staging contract; the completed temporary FLAC is parsed again before publication. An unreadable header or bit depth above 16 raises `NonStreamableError`, removes the invalid stage through the existing cleanup path, and never replaces an existing library file. Offline tests cover accepting 16-bit and rejecting 24-bit staging files.
- Additional changed file for the final guard: `streamrip/client/downloadable.py`. Focused probe/TIDAL/publication/retry validation after this addition: `27 passed`; full suite excluding the known order-sensitive Rich search module `221 passed, 7 skipped`; that module independently passed `5 passed`; Ruff clean. A new real download still requires separate user authorization.

### Live catalog preview: TIDAL album 6376804

- On 2026-09-05 the user requested a cross-service availability check for TIDAL album `6376804`. The isolated 2.2.8 client ran a dry-run only, expanded all 22 album tracks, disabled resume and manifest output, used two comparison workers, enforced a 16-bit ceiling, and preserved priority TIDAL → Deezer → Qobuz. No audio was downloaded and no library file was modified.
- All 22 tracks completed comparison without processing failures, duplicates, or resume skips. TIDAL was the only playable candidate returned for every track, at delivered codec `MP4A.40.5`; no verified Deezer candidate matched by ISRC or the metadata fallback search.
- A separate comparison-only check of the first album track (`6376805`) reproduced the same TIDAL-only `MP4A.40.5` result. Qobuz remained unavailable because the active account is not eligible to download, so this run cannot prove that TIDAL is the best catalog source—only that it is the sole candidate verifiable with the currently eligible accounts.
- No product code, configuration, credentials, commits, pushes, merges, publications, or media files were changed by this preview. A true three-service result remains blocked on an eligible Qobuz subscription/session.

### Restored three-service comparison and common physical guard

- The user replaced the private Deezer ARL through `rip login deezer --arl`; hidden entry and live validation succeeded. The credential remains only in the private app-data configuration and is not present in this repository or memory.
- Subsequent preview-only album checks performed no media download. TIDAL albums `16142328`, `549980023`, and `80233791` resolved respectively to 31 tracks (one TIDAL FLAC 16/44.1 and 30 TIDAL AAC), 14 TIDAL FLAC 16/44.1 tracks, and 13 TIDAL FLAC 16/44.1 tracks. Deezer did not provide a better exact match for those editions.
- A fresh explicit three-service comparison of TIDAL album/track `75920113` succeeded across every provider: TIDAL `75920114` delivered AAC, Qobuz `42124007` and Deezer `380127681` both delivered FLAC 16-bit/44.1 kHz by exact ISRC. Deezer won the technical tie under priority TIDAL → Deezer → Qobuz. Repeating with a 24-bit ceiling confirmed that no provider offers that recording above 16 bits. This supersedes the earlier Qobuz-ineligible observation; all three sessions currently report configured and Qobuz was playable in this check.
- Physical staging verification is now common to TIDAL, Qobuz, and Deezer rather than TIDAL-specific. CD-tier FLAC downloads carry 16-bit and 44.1 kHz publication limits. After transfer and before any final-path replacement, the shared staging contract parses FLAC STREAMINFO and rejects an unreadable header, excess bit depth, or excess sample rate through the existing invalid-stage cleanup path.
- `BasicDownloadable` accepts optional physical ceilings; Qobuz assigns them for quality tier 2, Deezer assigns them for its FLAC tier, and TIDAL assigns both limits to its verified LOSSLESS tier. Higher tiers and lossy formats remain compatible and do not receive CD-only limits.
- Changed product files: `streamrip/client/downloadable.py`, `streamrip/client/qobuz.py`, and `streamrip/client/tidal.py`; regression coverage expanded in `tests/test_audio_probe.py`.
- Validation: focused provider/probe/publication/retry tests `34 passed, 5 skipped`; full suite excluding the known order-sensitive Rich search module `223 passed, 7 skipped`; that module independently passed `5 passed`; Ruff clean and `git diff --check` reported only informational Windows line-ending notices. No real media download was performed.
- Next controlled step requires explicit authorization: download one 16-bit winner to trusted `Z:\`, then independently inspect codec, physical depth/rate, ISRC, tags, cover, final canonical path, hash, manifest/checkpoint state, and an empty recovery queue.

### Authorized three-service 16-bit publication validation

- The user explicitly authorized one real download of TIDAL album/single `75920113`. Before transfer, `rip destination status Z:\` passed and the recovery queue was empty. The isolated 2.2.8 client compared all three configured services with a 16-bit ceiling and priority TIDAL → Deezer → Qobuz.
- Exact-ISRC comparison found TIDAL `75920114` as AAC, Qobuz `42124007` as FLAC 16-bit/44.1 kHz/stereo, and Deezer `380127681` at the same FLAC quality. Deezer won only the configured technical tie-break and supplied the audio.
- The verified final file is `Z:\18 Kilates\(2017-07-28) Mirame\18 Kilates - Mirame.flac` (20,763,676 bytes). Independent Mutagen inspection confirmed native FLAC, 16-bit, 44,100 Hz, stereo, 182.250 seconds, one embedded picture, title/artist/album/album-artist/year/track/disc tags, and canonical ISRC `ARG991025122`. SHA-256: `0808F571E45940DECCDAF97A00DFCAFC0854E4BD5301757E4F343E470248DFCD`.
- Post-transfer destination identity still passed and `rip recovery list` remained empty, confirming successful verified publication with no retained stage. No other media was downloaded; no credential, global installation, push, merge, release, or publication was changed.
- This closes the recommended real 16-bit three-service selection/publication check. Remaining high-value validation is a small mixed-winner collection run plus simulated interruption/token-expiry recovery; neither requires altering this verified file.

### Bounded external-service waits and direct artist preview

- The authorized mixed-winner playlist exercise began with a no-download preview of the first 10 entries from TIDAL playlist `cd353f9c-d621-44b9-aa6e-a0497541d908`; TIDAL now delivered verified FLAC 16/44.1 for and won all ten. Resume correctly skipped those ten and processed seven additional TIDAL winners before external service calls stopped producing output. Both stalled preview attempts were interrupted safely; no audio, staging file, or recovery entry was created.
- A three-service comparison of playlist track `418065883` eventually completed after the slow session period: TIDAL `418065883`, Qobuz `318036800`, and Deezer `3234728701` all matched by exact ISRC at FLAC 16-bit/44.1 kHz/stereo; Deezer won when explicitly placed first. This proves configurable tie priority, but a mixed-winner download set should not be forced merely by changing priority when delivered quality is equal.
- The stalls exposed two unbounded wait surfaces. `MultiSourceComparator` now applies an independent 45-second timeout to each provider candidate search, so one hanging service becomes a report error without cancelling valid candidates from other services. Comparison/library CLI logins now also have a 45-second bound per service; reference-service timeout terminates truthfully while secondary timeouts are recorded as unavailable.
- Regression tests cover a hanging candidate provider coexisting with a successful provider and cancellation of a hanging login. Focused comparison/CLI/library tests: `39 passed`; full suite excluding the known order-sensitive Rich search module: `225 passed, 7 skipped`; that module independently passed `5 passed`; Ruff clean and `git diff --check` reported only informational Windows line-ending notices.
- Direct artist preview for TIDAL artist `3970137` (2 Chainz) successfully expanded the discography in streamed track mode and processed a bounded sample of 10 tracks. All ten selected TIDAL at verified FLAC 16-bit/44.1 kHz; failures, duplicates, and resume skips were zero. No audio was downloaded.
- Changed files: `streamrip/comparison.py`, `streamrip/rip/cli.py`, `tests/test_comparison.py`, and `tests/test_compare_cli.py`. Residual efficiency improvement: secondary logins are bounded but still initiated sequentially, so multiple slow providers can accumulate their individual limits; concurrent bounded login initialization is the next appropriate optimization.

### TIDAL HTTP 429 parity audit

- The sibling `../tiddl-elvigilante` implementation was audited without contacting any service. Its defense is layered: one shared fixed-interval request budget across TV and HiRes clients; adaptive delay after 429; `Retry-After` plus jitter; bounded exponential retry for 429/500/502/503/504; a shared 12-strike run-wide circuit breaker; cooperative cancellation that stops queued work and sleeping retry loops; resumable checkpoints; and non-zero CLI completion on a safety stop.
- Its most important preventive measure is client routing. For the ordinary 16-bit `high` tier, TV is the primary client for metadata/enumeration and LOSSLESS playback. HiRes is primary for `max`, or is used only for the specific per-track HiRes rung. Regression tests require an ordinary 16-bit track to make exactly one TV playback request.
- Streamrip already had a shared async request budget, adaptive delay, `Retry-After` handling, a 12-strike breaker, immediate rejection after a trip, and bounded request timeouts. The audit found a concrete parity defect: the lazily constructed TV lossless fallback inherited the primary request budget but not its `RateLimitGuard`, allowing primary and fallback clients to maintain independent strike counts.
- The fallback now receives the exact same `RateLimitGuard` object as the primary client. An offline regression test asserts both shared objects, so strikes from either token contribute to one safety decision.
- Quality-aware routing now sends an exact 16-bit ceiling to TV first. A valid TV FLAC returns immediately without a primary/HiRes playback request. If TV fails, the primary cascade remains available; if TV returns only lossy audio, that candidate is retained while primary gets one opportunity to improve it. TV is not requested twice. Higher ceilings keep the existing HiRes-first cascade and optional TV lossless fallback.
- Offline call-order coverage asserts the one-TV/zero-HiRes ordinary path and the TV-lossy/one-primary improvement path. Validation: focused TIDAL quality/auth/request-budget tests `20 passed`; full suite excluding the known order-sensitive Rich search module `228 passed, 7 skipped`; that module independently passed `5 passed`; Ruff clean; `git diff --check` reported only informational Windows line-ending notices. Changed files: `streamrip/client/tidal.py`, `tests/test_tidal_auth.py`, and `tests/test_tidal_quality.py`.
- Remaining resilience work: audit resume/dispatch semantics so a TIDAL breaker stops further TIDAL traffic without unnecessarily discarding already-enumerated tracks that Deezer or Qobuz can satisfy. The first controlled live validation should be comparison-only, one known 16-bit track, and must verify from debug/call instrumentation that TV served the TIDAL candidate without touching primary HiRes playback.

#### Controlled TV-first live attempt and debug-log hardening

- The user authorized one comparison-only attempt for TIDAL track `75920114` across all three services at a 16-bit/44.1 kHz ceiling. Trusted destination status passed beforehand; the command did not include download mode.
- Safe route instrumentation confirmed `TV first (16-bit ceiling)` before any primary playback request. The TV endpoint immediately returned repeated HTTP 429 responses, so the process was terminated after two backoff cycles rather than waiting through or adding further strikes. The comparison did not complete and no audio/staging/recovery file was created. This proves routing order but not a successful TV delivery while TIDAL remains throttled.
- Verbose output exposed a pre-existing security defect in Qobuz diagnostics: `_api_request` logged complete parameter values and file-URL signature construction logged raw/hashed signatures. The values themselves are intentionally omitted here. Product logs now emit only endpoint and sorted parameter names; raw and hashed signature logs were removed.
- A regression test executes a fake login request under debug logging and asserts that parameter names remain useful while token/user-ID values never appear. Safe TIDAL route messages now state TV-first and, on success, explicitly state that primary HiRes was not requested.
- Changed files after the routing commit: `streamrip/client/qobuz.py`, `streamrip/client/tidal.py`, and `tests/test_qobuz_client.py`. Focused security/TIDAL validation: `17 passed`; Ruff clean. Because one Qobuz authentication token was emitted by the old debug behavior during the controlled attempt, rotate/re-authenticate that private Qobuz session before treating it as confidential again.

### In-progress TIDAL 429 cross-service failover

- The user requested that a TIDAL 429 no longer abort comparison when Deezer or Qobuz may satisfy the same recording. An uncommitted implementation is in progress in `streamrip/rip/cli.py`, `streamrip/rip/main.py`, and `streamrip/library.py`.
- The proposed helper catches only `TidalRateLimitError` from TIDAL playback resolution. It preserves the already-fetched TIDAL catalog identity/ISRC, invokes the comparator without a TIDAL seed, records TIDAL as unavailable, and allows the comparator's independent Deezer/Qobuz tasks to select a candidate. The already-tripped shared TIDAL guard rejects its comparator branch before network access; the breaker is not reset or bypassed.
- Both direct `rip compare` and mass `rip library` have been wired toward this helper. Download-mode queuing now also carries the already-enumerated reference metadata so `PendingLibraryTrack` can construct canonical metadata after a TIDAL breaker without making another mandatory TIDAL track request.
- This work is not yet validated and must not be considered complete. No regression tests have been added for failover selection or cached-metadata file construction, and formatting/type/lint/full-suite checks have not yet run. The implementation should be reviewed for imports, API compatibility, concurrency behavior, manifest error reporting, and download-mode metadata/artwork/lyrics behavior before commit or live testing.
- No service request, credential change, download, commit, push, merge, or publication was performed while recording this update. No secret or destination anchor identifier is included.

#### Completed offline implementation

- The failover implementation is now complete offline. `rip compare` and `rip library` catch only the explicit `TidalRateLimitError` raised by the shared breaker during reference playback resolution. They reuse the catalog identity/ISRC and continue concurrent candidate resolution in Deezer and Qobuz; the tripped TIDAL branch fails before network access and remains listed as unavailable.
- `ComparisonCollection` now retains an in-memory ID-to-metadata map for album, playlist, mix, and artist expansion. Therefore later collection tracks do not require fresh TIDAL metadata requests after the breaker trips. Direct single-track comparison still requires one initial catalog response to learn title/artist/ISRC; no correct cross-service match is possible if TIDAL is already blocking before that identity is obtained.
- Download-mode library items carry their already-enumerated TIDAL metadata. If canonical track lookup is rejected by the breaker, `PendingLibraryTrack` uses this cached response, then downloads audio only from the selected Deezer/Qobuz client. This metadata is process-local and remains excluded from checkpoint and audit serialization.
- Regression coverage verifies catalog-identity failover without a reference candidate, collection metadata retention, and cached-metadata file construction after the TIDAL breaker. Focused comparison/CLI/library tests: `41 passed`; full suite excluding the known Rich search-order module: `231 passed, 7 skipped`; that module independently passed `5 passed`; Ruff clean and `git diff --check` reported only informational Windows line-ending notices.
- No external service, credential, destination, or media file was touched during implementation and validation. A live test should wait until the user requests it; the safest validation is a bounded preview whose TIDAL breaker is simulated offline, since deliberately generating twelve real 429 responses would be harmful and unnecessary.

#### Strict stop when reference information is unavailable

- The user requires fail-closed behavior when Streamrip cannot obtain enough trusted information to identify a recording. TIDAL 429 failover now proceeds only when the cached reference has either a non-empty ISRC or the complete conservative fallback tuple of source ID, title, and artist. If neither identity is available, `ReferenceIdentityUnavailableError` stops the comparison instead of attempting an unsafe approximate match.
- Mass-library processing promotes this identity failure to a fatal Click error. Exiting the bounded ordered dispatcher cancels pending workers through its existing cleanup contract, so the run does not silently continue building a partial library after reference identity is lost. Ordinary absence of a candidate on one secondary provider remains non-fatal because the other configured services may still satisfy the verified identity.
- Artist expansion no longer discards album-metadata exceptions returned by its concurrent batch. Any unavailable album response is propagated, preventing an incomplete discography from being presented as a complete artist library.
- Changed files: `streamrip/exceptions.py`, `streamrip/rip/cli.py`, `streamrip/comparison.py`, `tests/test_compare_cli.py`, and `tests/test_comparison.py`.
- Validation: changed-file Ruff checks passed; focused comparison/CLI/library tests `43 passed`; full suite reached `234 passed, 7 skipped` with four known order-sensitive Rich live-display failures in `tests/test_search_main.py`; that entire module independently passed `5 passed`. No external service call, credential operation, destination write, or media download was performed.

### Unified tiddl-compatible path templates

- The user's private placeholders remain unchanged: folder `{artist_initials}\\{albumartist}\\({year}) {title}` and track `{tracknumber:02}. {artist} - {title} {explicit}`. Only the non-secret `[filepaths]` section was inspected; no credential field was read or printed.
- Album and track formatting now expose the nested tiddl namespace uniformly after TIDAL, Deezer, or Qobuz metadata normalization. Supported album fields include `album.id/title/safe_title/artist/safe_artist/artists/safe_artists/date/explicit/master/release`; item fields include `item.id/title/safe_title/title_version/number/volume/version/artist/artists/features/artists_with_features/isrc/explicit/genre/releaseDate/streamStartDate`. Date formatting such as `{album.date:%Y}` and conditional explicit formatting such as `{item.explicit:shortparens}` follow tiddl semantics.
- Legacy Streamrip aliases remain compatible. Regression coverage proves the user's legacy folder/track patterns render identically to their nested tiddl equivalents, and that the same normalized recording produces the same result for TIDAL, Deezer, and Qobuz. Both `/` and `\\` template separators pass through the common component sanitizer and become the native platform separator.
- The bundled default config now advertises tiddl-native templates for new installations while existing private configs are not rewritten. The ordinary album path flow no longer applies a 150-character slice; like tiddl and the mass-library flow, it preserves the hierarchy and relies on the common per-component 255-byte Unicode-safe constraint.
- Changed files: `streamrip/config.toml`, `streamrip/media/album.py`, `streamrip/metadata/album.py`, `streamrip/metadata/track.py`, and new `tests/test_template_paths.py`.
- Validation: focused path/metadata/library/config tests `45 passed`; full suite excluding the known order-sensitive Rich module `238 passed, 7 skipped`; that module independently passed `5 passed`; Ruff and `git diff --check` passed apart from informational Windows line-ending notices. No service call, destination write, config mutation, or media download occurred.

#### Operational readiness check after template unification

- A read-only executable check confirmed that the isolated development command reports Streamrip `2.2.8`, exposes the expected `compare` and `library` options, and runs from branch `codex/multisource-comparison`. The repository was clean before this memory update.
- Invoking destination status caused Streamrip's existing automatic config migration to update the private schema from 2.0.6 to 2.2.0. The migration preserved the user's folder, track, and newly backfilled playlist placeholders, but unexpectedly reset `downloads.destination_identity` to `off`; it was immediately restored to the previously approved `strict` state. A subsequent destination status check passed. No destination anchor identifier is recorded here.
- Credential values were not displayed. Boolean-only inspection confirmed that TIDAL, Deezer, and Qobuz each have their required private configuration fields populated; this does not prove that their remote sessions are currently valid.
- The client is usable now through the isolated executable. Remaining release-readiness checks are one explicitly authorized three-service comparison without download, followed by one explicitly authorized bounded publication to the trusted destination with independent path, tags, physical-quality, manifest/checkpoint, and recovery-queue verification. The global `rip` command may still resolve to legacy 2.1.0 and should not be replaced until these checks pass.

#### Successful live readiness validation

- The user explicitly authorized both remaining live checks. Destination identity passed in strict mode and the recovery queue was empty before work began.
- A comparison-only run for TIDAL album/single `75920113` completed across all three configured sessions. Exact ISRC matching found TIDAL AAC, Qobuz FLAC 16-bit/44.1 kHz/stereo, and Deezer FLAC 16-bit/44.1 kHz/stereo. Deezer won the technical tie under priority TIDAL → Deezer → Qobuz. No audio was downloaded in this first check.
- A bounded publication run then processed exactly one previously unpublished recording from TIDAL album `5565055`, with tracks mode, one worker, no resume, a one-track ceiling, a 16-bit/44.1 kHz quality ceiling, and an isolated credential-free manifest. Deezer won by exact ISRC and supplied the FLAC audio; summary was processed 1, attempted 1, failed 0.
- The resulting path follows the user's configured placeholders exactly: `Z:\#\23 Skidoo\(1984) Urban Gamelan (Re-mastered)\01. 23 Skidoo - F.U.G.I.flac`. Independent Mutagen inspection confirmed native FLAC, 16-bit, 44,100 Hz, stereo, 341.627 seconds, one embedded picture, title/artist/album/album-artist/year/track/disc tags, and ISRC `GBHBR0404274`. File size is 38,123,734 bytes; SHA-256 is `C9155F466EEF7D93067239343544F1DFE8906438408D8CD7794B6FF38E6DC89B`.
- The isolated manifest contains exactly `selected` and `completed` states with consistent source, identity, quality, and final path. The matching resume checkpoint contains the completed ISRC. Post-publication destination status passed and the recovery queue remained empty.
- This closes the two release-readiness checks identified above. No code or credential was changed. The downloaded validation file remains in the trusted library. The only remaining operational choice is whether to keep invoking the isolated 2.2.8 executable explicitly or deliberately make it replace the legacy global `rip` 2.1.0 command.

### GitHub publication and global 2.2.8 installation

- The user explicitly requested publication and replacement of the installed 2.1.0 client. A final repository scan found only documented/test placeholder credentials and no real ARL, token, or long credential material. Branch `codex/multisource-comparison` was pushed to `origin` and GitHub PR #19 was opened against `main`: `https://github.com/np3ir/streamrip-elvigilante/pull/19`. No force push, merge, tag, or GitHub release was performed.
- The first editable global install exposed two packaging facts: upstream `streamrip` 2.1.0 and `streamrip-elvigilante` both owned the `rip` console entry point, so the old executable remained active; and inherited dependency ceilings downgraded shared `aiofiles`, `m3u8`, Pillow, and Rich versions, conflicting with the installed tiddl/tidmon clients.
- The obsolete upstream `streamrip` distribution was uninstalled, leaving the isolated 2.2.8 environment as rollback protection. Project dependency ranges were modernized to the shared-compatible families: `aiofiles >=23.2,<26`, `m3u8 >=5.1,<7`, `Pillow >=11.1,<13`, and `rich >=13.6,<16`; `poetry.lock` was regenerated. The global editable installation was then rebuilt without dependency downgrades.
- Global `rip --version` now reports 2.2.8, its import resolves to this repository, `rip compare --help` is available, and strict destination status passes. `tiddl-elvigilante 1.5.5` still launches. `pip check` no longer reports conflicts for Streamrip, tiddl, or tidmon; it still reports several pre-existing version conflicts owned by unrelated `deemon 2.22`.
- Validation against the modern global dependency set: full suite excluding the known order-sensitive Rich module `238 passed, 7 skipped`; that module independently `5 passed`; Ruff and `git diff --check` passed apart from informational Windows line-ending notices. No additional service request or media download was made during installation validation.
- Pending publication action: commit and push the dependency-range/lock update so PR #19 exactly matches the now-validated global installation. The PR remains open; merging to `main` and creating a release/tag remain deliberate follow-up actions.

#### Completed GitHub integration and installation replacement

- The dependency update was committed and pushed. GitHub's current Ruff initially identified one pre-existing union-order rule, which was corrected. Linux CI then exposed two real portability gaps hidden by the earlier Windows-only split run: `pick` was incorrectly platform-restricted, and Rich live spinners could collide with redirected/non-terminal output. `pick` is now cross-platform, Click is explicitly constrained to the stable modern `>=8.2,<9` family, and search/playlist status rendering uses a no-op context outside interactive terminals or when another live display is active.
- The full test suite now succeeds in one uninterrupted run with the modern global runtime: `243 passed, 7 skipped`; current Ruff passes. GitHub PR #19 completed with Python tests, both Ruff jobs, CodeQL analysis, and CodeQL all passing.
- PR #19 was merged into `main` at merge commit `fe3abb8e73412eec9f251d921f87b085af89085f`. Local `main` is synchronized with `origin/main`; the temporary local and remote feature branches were deleted. A zero-byte stale Git packed-refs lock created during branch cleanup was removed only after confirming no Git/GitHub process was active.
- The global upstream `streamrip` 2.1.0 distribution has been replaced. The ordinary `rip` command now reports 2.2.8 and imports this repository's merged `main`; strict `Z:\` destination status passes. The isolated 2.2.8 virtual environment remains available as rollback protection. No release/tag or PyPI publication was created.

### White Lion artist-scale operational check

- A live comparison-only invocation of `rip compare https://tidal.com/artist/13374` resolved the artist as White Lion and expanded 374 catalog tracks. It reached track 86 before being deliberately interrupted so it would not compete with the user's subsequent download attempt or continue accumulating rate-limit backoff. No media was downloaded by this comparison.
- The observed sample exercised all three configured services. Several recent catalog entries matched by exact ISRC with TIDAL and Qobuz at FLAC 24-bit/44.1 kHz and Deezer at FLAC 16-bit/44.1 kHz; Qobuz won those 24-bit comparisons. A later catalog entry matched all three at FLAC 16-bit/44.1 kHz and TIDAL won according to the configured service priority. The command entered bounded quiet backoff periods but did not expose credentials or fail with an unsafe approximate match.
- The user's private configuration now has `comparison.max_bit_depth = 16`, and the effective download root remains `Z:/`. The user's screenshot showed two artist-library attempts explicitly ending with `Aborted!`; a later 16-bit attempt selected its first TIDAL candidate but no `rip library` process remained active when inspected. No new White Lion media publication attributable to these attempts was found; existing White Lion folders predate this check.
- The recommended next validation is a bounded one-track run: `rip library https://tidal.com/artist/13374 --tracks --download --max-bit-depth 16 --max-tracks 1`, allowed to finish without interruption, followed by physical inspection of the resulting file beneath `Z:\W\White Lion`. No code, repository dependency, credential, commit, push, merge, tag, or release changed during this operational check.

#### Corrected multi-artist separator parity

- The bounded White Lion download completed at `Z:\W\White Lion\(2026) Gen X Tik Tok Trends\01. White Lion, Lioness - Gen X Trend.flac` (20,800,461 bytes). Comparing it with the same recording previously created by tiddl-elvigilante exposed a concrete naming mismatch: Streamrip joined artists with `, ` while tiddl used ` / `, which the common Windows sanitizer renders as the filename-safe full-width `／`.
- The active private configuration was changed only at the non-secret metadata field to `artist_separator = " / "`; the download root and credentials were not modified or exposed. Existing files were not renamed. Future filenames and embedded multi-artist tags will use the tiddl-compatible separator.
- Product defaults were aligned as well in `streamrip/config.py`, `streamrip/config.toml`, and `streamrip/metadata/util.py`, preventing new installations and code paths without explicit configuration from reverting to comma separation. Regression coverage was added in `tests/test_template_paths.py`; two library mock expectations in `tests/test_library.py` now reference the shared default constant rather than hard-coding the old separator.
- Focused template/config validation passed `18 passed`. The uninterrupted full suite passed `244 passed, 7 skipped`; Ruff passed and `git diff --check` reported only informational Windows line-ending notices. These code and test changes remain uncommitted on `main`; no commit, push, merge, release, additional service request, or media download was performed during the correction.

### LRC sidecar repair and live validation

- The active non-secret lyrics setting was found disabled and was changed to `lyrics.save_lrc = true`. Streamrip already supported TIDAL timed lyrics and Deezer synchronized lyrics, but `Track.rip()` returned immediately when the canonical audio existed, preventing creation of a missing sidecar. The existing-file path now calls the shared LRC writer before marking the item complete, allowing a later run to repair a missing `.lrc` without downloading or retagging the audio.
- Regression coverage in `tests/test_library.py` verifies that existing audio receives a same-basename UTF-8 LRC while the audio downloadable is never invoked. The LRC write remains guarded by the configured destination boundary. Focused library/template/config validation passed `37 passed`; subsequent Deezer/library validation passed `23 passed, 1 skipped`.
- Live negative checks used TIDAL track `498773237` and the first two tracks of album `543821869`. TIDAL returned `Not Found` from its authenticated lyrics endpoint for each checked recording, so no sidecar was created; this is catalog absence rather than a filesystem failure. The album check downloaded two bounded FLAC 16-bit/44.1 kHz tracks to the trusted destination while preserving the corrected multi-artist filename separator.
- A positive live check used Deezer album `240714`. The first recording (`Ámame`, ISRC-matched to an existing canonical FLAC) supplied synchronized lyrics through Deezer. Streamrip skipped the existing audio and created `Z:\J\Juanes\(2004) Mi Sangre\01. Juanes - Ámame.lrc` (2,469 bytes) with valid timestamped LRC lines. This verifies the requested sidecar behavior end to end without redownloading the FLAC.
- Verbose diagnosis exposed pre-existing Deezer debug statements that serialized complete playback dictionaries, including transient playback authorization material and signed URLs. Those values are intentionally excluded here. `streamrip/client/deezer.py` and `streamrip/client/downloadable.py` now log only track ID and quality tier, preserving diagnostics without sensitive structures.
- Final uninterrupted validation passed `245 passed, 7 skipped`; Ruff and `git diff --check` passed apart from informational Windows line-ending notices. Modified product files now include `streamrip/media/track.py`, `streamrip/client/deezer.py`, and `streamrip/client/downloadable.py`, plus the separator-default files already listed above. All changes remain uncommitted on `main`; no commit, push, merge, tag, or release was performed. Recommended next work is lyrics fallback from the reference service to another matched TIDAL/Deezer candidate, visible CLI status when lyrics are unavailable, per-run lyrics flags, and atomic sidecar replacement.

### Streaming comparison/download pipeline and completed lyrics follow-up

- `rip library` no longer accumulates every selected track before starting downloads. Download workers now remain active while the bounded ordered comparison producer runs; each verified selection is queued immediately. The queue has a finite capacity proportional to comparison workers, so slow downloads apply backpressure instead of allowing artist/playlist plans to grow without bound. At the end, the queue is drained and workers are cancelled and awaited cleanly; exceptional context exit also cancels them without marking unfinished tracks complete.
- `Main` now exposes persistent `start_download_workers` and `finish_download_workers` lifecycle methods while the ordinary `rip()` path preserves its previous behavior through those methods. A regression test proves a queued item is consumed before the streaming producer finishes, verifies the configured queue bound, and verifies clean worker shutdown. Checkpoint and manifest completion callbacks still run only after each individual media item finishes or is safely skipped as existing.
- Lyrics resolution now receives all verified TIDAL and Deezer candidates from the comparison report in deterministic TIDAL → Deezer order. It tries the next matched service when the earlier service has no lyrics, deduplicates identical service/track pairs, and reports `Lyrics unavailable` visibly when no candidate provides content. Qobuz remains excluded because it has no lyrics API.
- `rip library` now supports per-run `--save-lyrics` and `--no-save-lyrics`; the effective setting is included in the job signature so resume state cannot incorrectly cross lyrics policies. LRC publication now writes a temporary UTF-8 file in the destination directory, flushes and fsyncs it, then atomically replaces the final sidecar; temporary files are cleaned after failure.
- Offline focused validation passed `29 passed`, the streaming-worker module passed `20 passed`, and the final uninterrupted suite passed `248 passed, 7 skipped`; Ruff and `git diff --check` passed apart from informational Windows line-ending notices. A three-track live run completed successfully with immediate workers and visible lyrics absence; all three audio files already existed, so they were safely skipped rather than redownloaded.
- Product changes for this stage include `streamrip/rip/cli.py`, `streamrip/rip/main.py`, `streamrip/library.py`, `streamrip/media/lyrics.py`, and `streamrip/media/track.py`; regression changes include `tests/test_compare_cli.py`, `tests/test_library.py`, and new `tests/test_lyrics.py`. Changes remain uncommitted on `main`; no commit, push, merge, tag, or release was performed.

### Concurrent artwork publication on the trusted destination

- An artist-scale live run exposed a cross-volume artwork race: concurrent track workers for the same album downloaded the same embedded cover and one network-backed destination replace returned Windows `Access denied` after another worker had already published the final file. Audio processing was not implicated, and the verified staging copy was retained under the recovery contract.
- Cross-volume publication now treats an already-present destination as success only when its size and SHA-256 exactly match the verified staging source. It then removes the redundant local stage; a missing or different destination still raises `PublishError` and remains recoverable. Artwork scheduling also avoids downloading an already-present non-empty saved or embedded cover, reducing duplicate work before the generic publication safeguard is needed.
- Regression coverage in `tests/test_file_publish.py` simulates an `os.replace` permission failure after an identical concurrent publication and verifies that the final file remains intact, the source stage is removed, and no destination partial remains. Focused publication tests passed `12 passed`; the uninterrupted full suite passed `249 passed, 7 skipped`; Ruff and `git diff --check` passed apart from informational Windows line-ending notices.
- The one reported ZZ Top recovery entry could not use strict retry because it predated trusted destination identity metadata. A read-only comparison independently confirmed that its local stage, registry digest, and final destination all had identical size and SHA-256. Only that redundant stage and its registry record were then discarded through `rip recovery discard`; the final cover remains present. Other historical recovery entries were not changed.
- Modified files for this follow-up are `streamrip/file_publish.py`, `streamrip/media/artwork.py`, `tests/test_file_publish.py`, and this memory. The fix is validated locally but is not yet committed or pushed; no additional service request, media download, merge, tag, or release was performed.

### Non-destructive LRC preservation

- A subsequent live artist run revealed that every successful lyrics lookup replaced an existing same-basename `.lrc`. The sidecar writer now treats every non-empty existing LRC as user/library data and preserves it without modification. Automatic repair remains enabled only when the sidecar is missing or zero bytes.
- Regression coverage verifies that an existing audio file with a non-empty LRC keeps its original content even when newly fetched lyrics differ, and that its audio is not downloaded again. Focused library/lyrics tests passed `23 passed`; the uninterrupted full suite passed `250 passed, 7 skipped`; Ruff and `git diff --check` passed apart from informational Windows line-ending notices.
- Changed files for this correction are `streamrip/media/track.py`, `tests/test_library.py`, and this memory. Already overwritten LRC files cannot be reconstructed from Streamrip unless another backup or lyrics source retains the earlier content. The current long-running artist process loaded the prior implementation; a new `rip` process is required for this policy to take effect.

### Graceful TIDAL safety-stop rendering

- A real ZZ Top artist run reached the shared TIDAL 429 circuit-breaker after 219 selections. Previously completed downloads remained present, but the expected `TidalRateLimitError` escaped the CLI coroutine boundary as a Rich traceback and left the live progress display showing `Waiting for downloads` after the command had already returned to PowerShell.
- The common async CLI boundary now translates an otherwise-unhandled TIDAL breaker into a normal non-zero Click error stating that the run stopped safely and should be retried later. Its `finally` block always clears the progress display. The existing `Main` context cleanup still cancels queued/in-flight workers on exceptional exit, and only completion callbacks advance resume checkpoints.
- Regression coverage proves the breaker becomes `ClickException` and progress cleanup runs. Focused CLI/library validation passed `30 passed`; the uninterrupted full suite passed `251 passed, 7 skipped`; Ruff and `git diff --check` passed apart from informational Windows line-ending notices.
- Changed files are `streamrip/rip/cli.py`, `tests/test_compare_cli.py`, and this memory. This follow-up is validated locally but not yet committed or pushed. No external service request, credential change, media download, recovery mutation, merge, tag, or release was performed during the correction.

### Canonical album-date correction and metadata reuse

- The user identified `Z:\Z\ZZ Top\(2026) The Complete Studio Albums (1970 - 1990)` as historically incorrect. Local FLAC inspection confirmed `ALBUM=The Complete Studio Albums (1970 - 1990)` and `DATE=2026`. The run manifest ties these files to TIDAL reference tracks in album ID `493207974` while audio winners vary between Deezer and Qobuz.
- A bounded authenticated metadata check returned TIDAL album `493207974` with `releaseDate=2013-06-10`, `streamStartDate=2026-01-28`, 100 tracks, 10 volumes, and a 2013 copyright. Rhino's official release information independently identifies June 10, 2013. Therefore the correct canonical folder/tag year is 2013; 2026 is only this catalog edition's streaming availability date.
- Root cause: artist expansion fetched each complete album response, extracted its track list, then discarded the album response. Every download worker fetched the same album again. If this redundant request failed or met the TIDAL breaker, `_canonical_album` silently fell back to summarized track metadata whose only date was `streamStartDate`, producing the false 2026 year.
- `LibraryTrack` now carries process-local complete album metadata from album, mix, artist, and expanded-playlist enumeration into `PendingLibraryTrack`. Canonical metadata construction consumes that cached response before any network lookup. The value is excluded from stable serialization just like cached track metadata. For this 100-track box set, the change removes up to 100 redundant TIDAL album requests while preserving the authoritative 2013 release date across Deezer/Qobuz audio winners.
- Regression coverage verifies that expanded tracks retain the exact complete album response, that pending construction reuses it without another metadata request, and that it remains absent from serialized audit/checkpoint surfaces. Focused library/CLI tests passed `31 passed`; the uninterrupted full suite passed `252 passed, 7 skipped`; Ruff and `git diff --check` passed apart from informational Windows line-ending notices.
- Existing files under the incorrect 2026 directory were inspected read-only and have not been renamed or retagged. A controlled migration to the canonical 2013 path requires explicit user authorization and collision checks. The code correction is validated locally but not yet committed or pushed.

#### Post-correction library observation

- A later read-only check found the incorrect 2026 directory no longer present and the canonical 2013 directory present with 100 FLAC and 83 LRC files. Sampled ISRCs from discs 1, 3, and 8 match the reference ISRCs recorded in the Streamrip manifest. The user reports that a subsequent run downloaded again after the canonical year changed; no filesystem mutation was performed while recording this observation.
- Exact canonical-path existence checks prevent redownload only after metadata resolves to the same path. A wrong 2026 path and a correct pre-existing 2013 path are distinct, and the database alone cannot safely infer that they are interchangeable. A future library-index feature should detect existing tagged ISRCs created outside Streamrip, but it must be persistent/incremental rather than scanning the entire network volume for every track.
