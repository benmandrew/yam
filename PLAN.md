# Yam — a self-hosted YouTube video & playlist archiver

A lightweight, self-hosted app to archive YouTube **videos and playlists** by link,
store them locally, and play them back through a single web interface. Replaces a
heavyweight TubeArchivist install with something purpose-built and minimal.

## Goals

- Paste a YouTube **video or playlist** URL → it gets queued, downloaded, stored.
- One unified web UI for browsing/playing both standalone videos and playlists.
- Playlists are first-class: download a whole playlist, browse it, play through it.
- Runs as a small Docker Compose stack in a homelab. No external services required.

## Non-goals (for now)

- No channel-wide auto-subscription/sync (could come later — see Future).
- No transcoding-on-the-fly / adaptive streaming. We archive a browser-friendly file.
- No multi-user accounts. Single trusted user on a LAN (optional basic auth).

---

## Architecture

Single-container-friendly stack, no Redis/Celery:

- **Backend:** FastAPI (Python). yt-dlp is a Python library, so we import it directly
  and use progress hooks instead of shelling out and scraping stdout.
- **DB:** SQLite (WAL mode) via SQLModel/SQLAlchemy. Plenty for a single-user archive.
- **Job queue:** a DB-backed jobs table + an in-process async worker loop. Jobs persist
  across restarts (a `queued`/`running` job is re-picked on boot). One config-limited
  concurrency (default 1–2 simultaneous downloads).
- **Frontend:** server-rendered Jinja2 templates + **htmx** for live job/progress
  updates without a full SPA. **video.js** for the player (seeking, subtitles, speed).
- **Media processing:** ffmpeg (bundled in the image) for muxing separate video+audio
  streams into MP4 and converting thumbnails to jpg.

### Why these choices
- FastAPI + yt-dlp keeps everything in one language and gives real download progress.
- SQLite + in-process worker means **one container, one volume, no broker** — the
  lightweight footprint the user actually wants.
- htmx avoids a build step and a JS framework while still giving a live download queue.

### Container topology
Single image running **both** the web server and the worker (worker as an asyncio task
started on app startup). Compose mounts two volumes: media and app-data (DB). If download
throughput ever needs isolation, the worker can be split into its own service sharing the
same volumes — the DB-backed queue already supports that with no code change.

---

## Data model (SQLite)

- **video**
  - `id` (YouTube video id, PK), `title`, `channel`, `channel_id`, `description`,
    `duration_s`, `upload_date`, `thumbnail_path`, `file_path`, `filesize`,
    `width`/`height`, `ext`, `downloaded_at`, `status` (`present`/`missing`/`error`)
- **playlist**
  - `id` (YouTube playlist id, PK), `title`, `channel`, `description`,
    `thumbnail_path`, `added_at`, `last_synced_at`
- **playlist_video** (many-to-many; a video can live in multiple playlists)
  - `playlist_id`, `video_id`, `position` (playlist order)
- **job**
  - `id`, `type` (`video`|`playlist`), `url`, `target_id` (video/playlist id once known),
    `status` (`queued`|`running`|`done`|`error`|`skipped`), `progress` (0–100),
    `speed`, `eta`, `error_msg`, `created_at`, `updated_at`,
    `parent_job_id` (playlist jobs spawn one child job per video)

**Dedup rule:** videos keyed by YouTube id. If a video is already `present`, adding it via
another playlist just creates the `playlist_video` link — no re-download.

---

## yt-dlp integration

- **Format (universal playback, no re-encode):**
  `bv*[vcodec^=avc1]+ba[acodec^=mp4a]/b[ext=mp4]/bv*+ba/b`, merged to `mp4/webm/mkv`.
  Prefers **H.264 video + AAC audio in mp4** so files play in **Safari/iOS** and every
  browser; falls back to any mp4, then best-available. **Never re-encode** — muxing only.
  Tradeoff: H.264 caps at ~1080p (YouTube offers no H.264 above that), so this is *not*
  strictly max quality. (History: we started at max-quality mkv, switched to webm/mp4,
  then to H.264/mp4 after AV1/webm wouldn't play in Safari.)
- **Metadata:** `writeinfojson`, `writethumbnail` + convert to jpg, capture description,
  upload date, channel, duration, resolution from the info dict.
- **Output template:** store by id to avoid filename headaches:
  `/media/videos/%(id)s/%(id)s.%(ext)s` (typically `.mkv`) plus sibling `thumbnail.jpg` /
  `info.json`. The `ext` is stored per-video in the DB since it varies by source codec.
- **Progress:** register a `progress_hook` that writes `progress`/`speed`/`eta` to the
  job row so htmx can poll it.
- **Playlist enumeration:** first pass with `extract_flat=True` to list entries cheaply,
  create the `playlist` row + a child `job` per entry, then download each entry.
- **Optional:** subtitles (`writesubtitles`/`writeautomaticsub`), sponsorblock chapters,
  a mounted `cookies.txt` for age-restricted / members-only content (env-configurable).

---

## HTTP surface

### Pages
- `GET /` — library: tabs for **Videos** and **Playlists**, search/sort.
- `GET /watch/{video_id}` — video.js player + metadata + "playlists this belongs to".
- `GET /playlist/{playlist_id}` — playlist view, ordered videos, download-status per entry.
  Playback is **manual next**: a "Next ▸" control advances to the following entry; no
  autoplay chaining.
- `GET /downloads` — live job queue (htmx-polled progress bars).

### API
- `POST /api/download` `{url}` — detect video vs playlist from the URL, enqueue job(s).
- `GET /api/jobs` — queue + progress (htmx partial).
- `GET /api/videos`, `GET /api/videos/{id}`
- `GET /api/playlists`, `GET /api/playlists/{id}`
- `POST /api/playlists/{id}/sync` — re-enumerate, download newly added entries.
- `DELETE /api/videos/{id}` — delete file(s); remove playlist links; keep or purge row.
- `DELETE /api/playlists/{id}` — unlink; optionally delete videos not in other playlists.
- `GET /media/{video_id}` — stream the file **with HTTP Range support** (seeking).
  Use FastAPI/Starlette `FileResponse` (handles Range) or a small range handler.

---

## Storage layout

```
/media/
  videos/<video_id>/<video_id>.mkv   (ext varies by source codec)
  videos/<video_id>/thumbnail.jpg
  videos/<video_id>/info.json
/data/
  yam.db            (SQLite, WAL)
  cookies.txt       (optional, mounted)
```

Playlists are pure DB relationships — no file duplication; a playlist "contains" videos
by reference.

---

## Configuration (env vars)

- `MEDIA_DIR` (default `/media`), `DATA_DIR` (default `/data`)
- `MAX_CONCURRENT_DOWNLOADS` (default `2`)
- `VIDEO_QUALITY` / format override (default the MP4 selector above)
- `DOWNLOAD_SUBTITLES` (bool), `COOKIES_FILE` (path, optional)
- `BASIC_AUTH_USER` / `BASIC_AUTH_PASS` (optional gate)

---

## Docker

- **Dockerfile:** slim Python base + `ffmpeg` + `yt-dlp` (pinned, easy to bump) + app.
- **docker-compose.yml:** one service, ports, two named volumes (`media`, `data`),
  env file. Healthcheck on `/healthz`. Restart policy `unless-stopped`.

### Tailscale (recommended access method)
The app is plain HTTP on a port; Tailscale handles access with no app changes. Two options:
- **Sidecar (preferred):** add a `tailscale/tailscale` service (hostname `yam`,
  `TS_AUTHKEY`, state volume, `cap_add: net_admin`) and run the app with
  `network_mode: service:tailscale`. The stack joins the tailnet as node `yam`,
  reachable at `yam.<tailnet>.ts.net`, independent of the host.
- **Host-level:** Tailscale on the Docker host, bind a host port; reach via MagicDNS/100.x.
- **HTTPS:** `tailscale serve` fronts it with a real Let's Encrypt cert
  (`https://yam.<tailnet>.ts.net`) — nice for PWA install. **Keep Funnel OFF** (tailnet-only).
- yt-dlp's outbound traffic is unaffected; Tailscale only gates inbound UI access.

---

## Edge cases & decisions

- **Re-download detection:** skip videos already `present` (job → `skipped`).
- **Filenames:** always id-based on disk; human titles live in the DB/UI only.
- **Deletion semantics (full delete):** deleting a video removes the file(s) **and the DB
  row** — no "missing" tombstone. A video still linked by another playlist is protected
  (the file isn't removed while any playlist references it). Deleting a playlist removes
  the playlist and its links, and fully deletes any videos left with no other playlist
  reference.
- **Playback compatibility:** the format policy prefers H.264/AAC mp4, which plays in
  Safari/iOS and every browser. Rare fallbacks (a video with no H.264, or exotic codec
  mixes) may land as webm/mkv and won't play in Safari — the DB stores codec info so the
  UI can badge those. An optional on-demand "transcode to mp4" action could cover them
  later (out of MVP scope).
- **Playlist drift:** playlists change upstream; `sync` adds new entries, marks removed
  ones without deleting local files.
- **Crash recovery:** on boot, reset `running` jobs to `queued` and resume.
- **Disk safety:** surface free-space; optional min-free-space guard before starting a job.

---

## Milestones

1. **Skeleton** — FastAPI app, SQLite models, Docker + compose, `/healthz`, base layout.
2. **Single-video download** — `POST /api/download`, job worker, yt-dlp MP4 + metadata,
   `video` rows, `/downloads` progress UI.
3. **Playback** — `/media/{id}` Range streaming, `/watch/{id}` with video.js, library grid.
4. **Playlists** — enumeration, `playlist`/`playlist_video` models, per-entry child jobs,
   `/playlist/{id}` view + "play all", dedup across playlists.
5. **Management** — delete/orphan handling, playlist `sync`, search/sort, disk stats.
6. **Polish** — optional basic auth, cookies support, subtitles, config surface, README.

MVP = milestones 1–4. Ship those, then iterate on 5–6.

---

## Decisions (locked)

- **Quality/compatibility:** prefer **H.264/AAC mp4** for universal playback
  (Safari/iOS included), **no re-encode**; ~1080p cap accepted. (Revised from the original
  "always max quality" after AV1/webm wouldn't play in Safari.)
- **"Play all":** manual next — a "Next ▸" control, no autoplay chaining.
- **Retention:** full delete — remove files *and* DB rows; protect files still referenced
  by another playlist.
- **Access:** tailnet-only via Tailscale (sidecar container preferred); app-level auth
  skipped for MVP, relying on Tailscale ACLs.

## Open questions

- None blocking MVP. Optional basic auth remains available as later defense-in-depth if
  wanted; on-demand MP4 transcode is a post-MVP nicety.
