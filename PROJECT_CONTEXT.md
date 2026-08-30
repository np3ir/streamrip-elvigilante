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

Repository-local Git identity is configured as the existing project author. No global identity was changed and no push occurred.
