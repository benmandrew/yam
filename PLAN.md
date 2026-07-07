# Yam — roadmap (further work)

The MVP (milestones 1–4) is **complete**: Nix/Docker/Tailscale scaffolding + DB schema;
single-video downloads via the background worker with a live `/downloads` page; playback
(`/media` Range streaming, `/watch` player, thumbnail library grid); and playlists
(enumeration, ordered links, child jobs with cross-playlist dedup, manual "Next ▸").

Architecture, code structure, and build/format/lint commands live in `CLAUDE.md` and
`README.md`. **This file now tracks only the remaining work.**

## Decisions still in force (respect these)

- **Retention = full delete:** deleting removes files *and* DB rows; a video referenced by
  another playlist is protected until no playlist references it.
- **Format = prefer H.264/AAC mp4, no re-encode** (Safari/iOS + universal playback, ~1080p
  cap). Rare fallbacks land as webm/mkv.
- **Access = tailnet-only** via Tailscale; app-level auth is optional/defense-in-depth.
- **Playlist playback = manual "Next ▸"**, no autoplay chaining.
- **Not-yet-downloaded playlist entries** are `missing` Video rows (hidden from the
  library, shown as "pending" in the playlist).

## Milestone 5 — Library & job management

- **Delete video** (`DELETE /api/videos/{id}`): remove files + row, but only when no
  playlist still references it (otherwise unlink/guard per the full-delete rule).
- **Delete playlist** (`DELETE /api/playlists/{id}`): remove playlist + its links, and
  full-delete any videos left with no other playlist reference.
- **Job controls on `/downloads`:** retry a failed/errored job; clear finished jobs.
- **Library search + sort** (by title/channel; order by date/duration/size).
- Delete affordances (with confirm) on the library, watch, and playlist pages.

## Milestone 6 — Playlist sync & polish

- **Sync** (`POST /api/playlists/{id}/sync`): re-enumerate, enqueue newly added entries,
  mark removed ones — without deleting local files.
- **Retry pending/failed entries** directly from the playlist view.
- **Playlist thumbnails:** populate `Playlist.thumbnail_path` (e.g. first entry's thumb)
  and show it on the library card.
- **Downloads grouping:** show a playlist's parent job with its child video jobs nested.

## Milestone 7 — Ingestion & access polish

- **Cookies:** document + support mounting `cookies.txt` (already plumbed via
  `COOKIES_FILE`); surface the "sign in to confirm you're not a bot" error with guidance.
- **Subtitles:** when `DOWNLOAD_SUBTITLES` is set, fetch subs and expose `<track>` in the
  player.
- **Optional basic auth** (`BASIC_AUTH_USER`/`BASIC_AUTH_PASS`) as defense-in-depth atop
  Tailscale.
- **Disk stats + guard:** show total archived size / free space; refuse to start a job
  below a configurable min-free-space threshold.
- **Config view:** read-only page showing the effective settings.

## Milestone 8 — Testing & CI

- **pytest suite:** URL classification, dedup, `_save_video`/`_save_playlist`, `/media`
  Range streaming, next-in-playlist. (These are currently validated only manually via
  throwaway scripts.)
- **CI:** GitHub Actions running `nix flake check`, `ruff check`, and the test suite on PRs.
- Structured logging configuration.
- Worth pulling earlier — everything above ships more safely with a test net.

## Backlog (unscheduled)

- **On-demand "transcode to mp4"** for the rare webm/mkv fallback and any legacy AV1 files.
- Pagination / lazy-loading for large libraries.
- Optional video.js player (subtitle/quality UI) in place of native `<video>`.
- Bulk actions (multi-select delete).

## Explicit non-goals (unchanged)

- Channel-wide auto-subscription/sync.
- On-the-fly transcoding / adaptive streaming.
- Multi-user accounts.
