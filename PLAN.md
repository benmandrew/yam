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
- Pagination / lazy-loading for large libraries.
- Optional video.js player (subtitle/quality UI) in place of native `<video>`.
- Bulk actions (multi-select delete).

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

## Explicit non-goals (unchanged)

- Channel-wide auto-subscription/sync.
- On-the-fly transcoding / adaptive streaming.
- Multi-user accounts.
