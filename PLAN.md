# Yam — roadmap (further work)

Milestones 1–8 are **complete**:

- **M1–4 (MVP):** Nix/Docker/Tailscale scaffolding + DB schema; single-video downloads via
  the background worker with a live `/downloads` page; playback (`/media` Range streaming,
  `/watch` player, thumbnail library grid); and playlists (enumeration, ordered links, child
  jobs with cross-playlist dedup, manual "Next ▸").
- **M5 (library & job management):** delete video (guarded while referenced) and delete
  playlist (full-deletes orphaned videos), retry/clear on `/downloads`, library search + sort.
- **M6 (playlist sync & polish):** `POST /api/playlists/{id}/sync` re-enumerates and prunes
  removed links (files kept), retry-pending re-queues missing entries, playlist cover
  thumbnails, and `/downloads` nests child video jobs under their parent playlist job.
- **M7 (ingestion & access polish):** cookies guidance on the bot-check error, WebVTT
  subtitles with a player `<track>`, optional HTTP Basic auth, a `MIN_FREE_SPACE_MB` disk
  guard, and a read-only `/config` view. (Details struck through under Milestone 7 below.)
- **M8 (testing & CI):** offline `pytest` suite (`tests/`) covering URL classification,
  dedup, `_save_video`/`_save_playlist`, `/media` Range streaming, and next-in-playlist;
  `nix flake check` runs ruff + format-check + pytest in a sandbox; GitHub Actions runs it
  on PRs; structured logging via `yam/logging_config.py`.

Architecture, code structure, and build/format/lint commands live in `CLAUDE.md` and
`README.md`. **This file now tracks only the remaining work (the backlog below).**

## Decisions still in force (respect these)

- **Retention = full delete:** deleting removes files *and* DB rows; a video referenced by
  another playlist is protected until no playlist references it.
- **Format = prefer H.264/AAC mp4, no re-encode** (Safari/iOS + universal playback, ~1080p
  cap). Rare fallbacks land as webm/mkv.
- **Access = TLS terminated on the host** (the container speaks plain HTTP on 8080);
  `tailscale serve` is the reference front-end but any reverse proxy works. App-level auth
  is optional/defense-in-depth.
- **Playlist playback = manual "Next ▸"**, no autoplay chaining.
- **Not-yet-downloaded playlist entries** are `missing` Video rows (hidden from the
  library, shown as "pending" in the playlist).

## Milestone 7 — Ingestion & access polish

- ~~**Cookies:** document + support mounting `cookies.txt` (already plumbed via
  `COOKIES_FILE`); surface the "sign in to confirm you're not a bot" error with guidance.~~
  **Done:** `friendly_error` maps the bot-check failure to cookies guidance on failed jobs;
  README documents exporting/mounting `cookies.txt`.
- ~~**Subtitles:** when `DOWNLOAD_SUBTITLES` is set, fetch subs and expose `<track>` in the
  player.~~ **Done:** en subs fetched as WebVTT, stored on `Video.subtitle_path`, served at
  `/media/{id}/subtitles`, and rendered as a `<track>` in the player.
- ~~**Optional basic auth** (`BASIC_AUTH_USER`/`BASIC_AUTH_PASS`) as defense-in-depth atop
  Tailscale.~~ **Done:** HTTP Basic middleware (constant-time compare) guards every route
  except `/healthz`; active only when both vars are set.
- ~~**Disk stats + guard:** show total archived size / free space; refuse to start a job
  below a configurable min-free-space threshold.~~ **Done:** `disk.py` reports archived/free/
  total; the worker refuses video jobs under `MIN_FREE_SPACE_MB`.
- ~~**Config view:** read-only page showing the effective settings.~~ **Done:** `/config`
  shows effective settings + storage stats.

**M7 complete.**

## Milestone 8 — Testing & CI

- ~~**pytest suite:** URL classification, dedup, `_save_video`/`_save_playlist`, `/media`
  Range streaming, next-in-playlist.~~ **Done:** `tests/` (offline; TestClient runs without
  the lifespan so the worker/network never start). Test DB/media point at a temp dir set in
  `tests/conftest.py` before any `yam.*` import.
- ~~**CI:** GitHub Actions running `nix flake check`, `ruff check`, and the test suite.~~
  **Done:** `checks.tests` in the flake runs ruff + `ruff format --check` + pytest in a
  sandbox; `.github/workflows/ci.yml` runs `nix flake check` on PRs and pushes to `main`.
- ~~Structured logging configuration.~~ **Done:** `yam/logging_config.py` (`LOG_LEVEL`-driven,
  called from the app lifespan).

**M8 complete.**

## Backlog (unscheduled)

- **On-demand "transcode to mp4"** for the rare webm/mkv fallback and any legacy AV1 files.
- Optional video.js player (subtitle/quality UI) in place of native `<video>`.

## Thumbnail sizing

**Done:** yt-dlp fetches YouTube's full-res thumbnail (up to 1280×720), but the grid renders
each at ~240px, so cards loaded slowly. `yam/thumbnails.py::resize_thumbnail` downscales JPEGs
to ≤480px wide (2× the card, for retina) via ffmpeg — idempotent (skips already-small images,
never upscales, atomic temp-file `os.replace`). The worker resizes on download (`_save_video`),
and `worker.py::backfill_thumbnails` (run once off the event loop at startup) downscales any
pre-existing oversized thumbnails.

## Download phases

**Done:** the progress bar used to track only the byte download, so a job sat at 100% for as long
as ffmpeg took to mux. `Job.phase` (a `JobPhase` enum, nullable so `db.py::_add_missing_columns`
migrates existing databases) names the current stage and the bar restarts at 0% for each one:
`downloading_video`, `downloading_audio` (a merged download fetches the two streams in sequence,
each with its own 0..100 run), `merging`, then `finishing`.

The merge percentage is an estimate. yt-dlp's `postprocessor_hooks` report the merger starting and
finishing but carry no percentage, and `FFmpegPostProcessor` does not parse ffmpeg's progress
output at all. Since the merge is a stream copy, the output ends up about the size of the two input
streams, so `downloader.py::MergeProgress` samples the download directory's total size every 0.5s
and measures its growth past the pre-merge baseline, capped at 99%. That needs no knowledge of
yt-dlp's temporary file names. The sampler runs on a daemon thread that `download_video` always
joins before returning. `finishing` covers the remaining postprocessors and `_save_video`; it has
no measurable size, so its bar sweeps instead of filling.

## Custom playlists

**Done:** user-created playlists (`Playlist.origin`, nullable/default-less for safe
auto-migration — `None` reads as `youtube`) alongside YouTube-sourced ones. Created via a
small form on `/` (`POST /api/playlists`), populated by adding already-archived videos from
the `/` grid or `/watch/{id}` (`POST /api/videos/{id}/add-to-playlist`), and removed one at a
time from `/playlist/{id}` (`POST /api/playlists/{id}/videos/{id}/remove`). Custom playlists
always display sorted by the video's original YouTube upload date (no manual reorder), are
visually badged as "Custom" everywhere playlists are listed, and skip Sync/Retry (YouTube-only
concepts). Deleting a custom playlist never orphan-deletes its videos — see
`yam/library.py::delete_playlist`.

## Pagination & bulk actions

**Done:** the Videos section of `/` pages at 60 videos (`?page=N`, Prev/Next, count via a SQL
`func.count()` query rather than fetching the full result set); search and sort compose
correctly with the paginated total. Playlists stay unpaginated. Each video card also has a
checkbox (associated to a toolbar `<form id="bulk-form">` via the HTML5 `form=` attribute,
since the grid can't be wrapped in a form without breaking the existing per-card quick-add
forms) to bulk **add to a custom playlist** (`POST /api/videos/bulk-add-to-playlist`) or
**delete** (`POST /api/videos/bulk-delete`, same referenced-by-a-playlist guard as single
delete) selected videos at once.

## Keeping yt-dlp current

**Done:** the Docker image baked in whatever yt-dlp release pip resolved at build time, and
YouTube breaks yt-dlp's *extractor clients* on its own schedule. On 2026-08-17 YouTube began
returning 403 for the `android_vr` client, yt-dlp dropped that client from its defaults on
2026-08-19, and an image built on 2026-08-08 carried on asking for `android_vr` indefinitely.
Picking up a yt-dlp release used to require a commit to yam. Two mechanisms now do it without
one.

**Version-gated daily rebuild.** `.github/workflows/ytdlp-rebuild.yml` runs at cron
`0 4 * * *` and calls `ci.yml` through `workflow_call`. The schedule began inside `ci.yml`, but
GitHub disables any workflow with a `schedule` trigger after 60 days without repository
activity, and a disabled workflow ignores pushes as well. That stopped every push build from
2026-10-30 to 2026-11-16, so the schedule now has its own file. If GitHub disables it, only the
daily rebuild stops, and `gh workflow enable ytdlp-rebuild.yml` restores it. A called workflow
sees the caller's `github.event_name`, so the checks on `schedule` below still work.

`ci.yml` has a `ytdlp` job (skipped on `pull_request`). That job curls
`https://pypi.org/pypi/yt-dlp/json` for the current stable release and reads the
`io.yam.ytdlp.version` label off the published `:latest` with `docker buildx imagetools
inspect`, which is a registry read and pulls no layers. It outputs `version` and `stale`. The
`build` job now declares `needs: [test, ytdlp]` and runs only when the trigger is not
`pull_request` and either the trigger is not `schedule` or `needs.ytdlp.outputs.stale ==
'true'`, so a scheduled run that finds nothing new costs one curl and one registry read. Every
other trigger builds regardless, because the source tree changed.

**Pinning and the label.** The resolved version reaches the `Dockerfile` as the
`YTDLP_VERSION` build arg, which writes it into a pip *constraints file* so both architectures
of one multi-arch build pin the same release, and stamps it as the `io.yam.ytdlp.version`
image label. A bare local `docker build` leaves the arg empty, floats to whatever pip
resolves, and labels the image `unknown`, which the workflow reads as stale and rebuilds.
Writing the label from the same value that was pinned is what keeps the two honest. This
closes the *build* gap to about a day; the host running `:latest` still needs a `docker
compose pull` to receive the result.

**Runtime overlay.** `$DATA_DIR/ytdlp` is an overlay directory populated by
`POST /api/ytdlp/update`, which runs `pip install --target $DATA_DIR/ytdlp
yt-dlp==<current stable>` behind a "Check for update" button on `/config`.
`yam/ytdlp_update.py::ensure_overlay_on_path` is called from `yam/__init__.py`, since package
import is the only point guaranteed to run before `yam.downloader` imports `yt_dlp` at module
scope, and it prepends the overlay to `sys.path` only when the overlay's version is strictly
newer than the baked-in one. A rebuilt image shipping something newer therefore takes over on
its own and the overlay stops shadowing it, with no volume to clear by hand. A broken overlay
is logged and ignored rather than stopping the app booting.

**Reading and applying.** The overlay version comes from the `.dist-info` directory name, so
nothing has to be imported to find out what is there. A newly installed release takes effect
on the next restart, because `yt_dlp` is already imported by then, and the flash message says
so. Only stable PyPI releases are fetched — PyPI does carry yt-dlp nightlies as `.devN`
pre-releases, and yam never asks for them. `/config` shows the running yt-dlp version and
whether it came from the image or the overlay, and the app lifespan logs `yt-dlp <version>
active` once logging is configured.

The two halves overlap on purpose. The daily rebuild keeps a fresh install current with no
intervention, and the overlay covers the interval between a yt-dlp release and whenever the
host next pulls.

## Explicit non-goals (unchanged)

- Channel-wide auto-subscription/sync.
- On-the-fly transcoding / adaptive streaming.
- Multi-user accounts.
