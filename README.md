# Yam

Self-hosted app to archive YouTube **videos and playlists** by link, store them
locally, and play them back through one web interface. See [PLAN.md](./PLAN.md)
for the full design and roadmap.

> **Status:** Milestones 1–6 done — downloads, playback, playlists, library/job
> management, and playlist sync. M7 underway (cookies guidance + subtitles). See
> [PLAN.md](./PLAN.md) for the remaining roadmap.

## Features

- **Archive by link** — paste a YouTube video or playlist URL; a background
  worker (`MAX_CONCURRENT_DOWNLOADS` in parallel) fetches it with yt-dlp,
  preferring H.264/AAC mp4 (~1080p cap) for universal playback.
- **Live downloads page** (`/downloads`) — job status with retry, clear, and
  playlist jobs nesting their per-entry child jobs.
- **Library grid** (`/`) — thumbnail cards with search and sort (by
  title/channel, date/duration/size).
- **Playback** — native `<video>` player at `/watch/{id}` backed by Range
  streaming from `/media/{id}`.
- **Playlists** (`/playlist/{id}`) — ordered entries, cover thumbnail, manual
  "Next ▸", one stored file per video shared across playlists, and **sync** to
  re-enumerate (adding new entries, pruning removed links without deleting files).
- **Management** — delete a video (guarded while any playlist references it) or a
  full playlist (full-deletes orphaned videos), and retry pending playlist entries.

## Requirements

Dependencies are managed entirely by the Nix flake (Python, FastAPI, yt-dlp,
ffmpeg, …). You only need [Nix](https://nixos.org/download) with flakes enabled.

## Develop

```sh
nix develop            # enters devShell; sets MEDIA_DIR/DATA_DIR to ./.local
uvicorn yam.main:app --reload --port 8080
```

Then open http://localhost:8080 (and http://localhost:8080/healthz).

You can also run the packaged app directly, without a shell:

```sh
MEDIA_DIR=./.local/media DATA_DIR=./.local/data nix run .#yam
```

## Build & publish the container image

The image is built from the `Dockerfile` (works on any Docker host):

```sh
docker build -t yam:latest .
```

CI (`.github/workflows/docker-publish.yml`) builds and **pushes to Docker Hub**
on every push to `main` and on `v*` tags. Set two repo secrets:

- `DOCKERHUB_USERNAME` — your Docker Hub username (also the image namespace)
- `DOCKERHUB_TOKEN` — a Docker Hub access token

The published image is `docker.io/<DOCKERHUB_USERNAME>/yam` (tags: `latest`, the
git tag, and the short commit SHA). Reference it as the `image:` in compose.

## Deploy

`docker-compose.yml` runs a single container and publishes port **8080**; Yam
speaks plain HTTP, so put TLS / access control in front of it. If the host is
already a tailnet node, front it with host-level `tailscale serve` (any reverse
proxy works too):

```sh
tailscale serve --bg --https=8449 http://127.0.0.1:8080
#   -> https://<host>.<tailnet>.ts.net:8449
docker compose up -d
```

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `MEDIA_DIR` | `/media` | Where video files/thumbnails are stored |
| `DATA_DIR` | `/data` | SQLite DB location |
| `MAX_CONCURRENT_DOWNLOADS` | `2` | Parallel download workers |
| `DOWNLOAD_SUBTITLES` | `false` | Fetch subtitles alongside videos |
| `COOKIES_FILE` | — | Path to cookies.txt for restricted content |
| `MIN_FREE_SPACE_MB` | `500` | Refuse to start a download below this free space (`0` disables) |
| `BASIC_AUTH_USER` | — | Username for optional HTTP Basic auth |
| `BASIC_AUTH_PASS` | — | Password for optional HTTP Basic auth |

The effective settings and storage stats are visible read-only at `/config`.

The app listens on port **8080** inside the container/package. To use a different
port in local dev, pass it to uvicorn: `uvicorn yam.main:app --port 9000`.

### Subtitles

Set `DOWNLOAD_SUBTITLES=true` to fetch the English subtitle track (manual, else
auto-generated) as WebVTT alongside each new video; the player then shows a
selectable subtitle track. Already-archived videos are unaffected — re-download
them to pick up subs.

### Cookies (restricted / age-gated content)

Some videos, and increasingly ordinary ones, return *"Sign in to confirm you're
not a bot"*. To get past it, export a `cookies.txt` from a browser where you're
signed in to YouTube (e.g. the "Get cookies.txt" extension, Netscape format),
mount it into the container, and point `COOKIES_FILE` at it:

```yaml
# docker-compose.yml
    environment:
      COOKIES_FILE: /data/cookies.txt   # mounted alongside the DB volume
```

When a download or playlist enumeration hits the bot check, the failed job on
`/downloads` shows this guidance instead of the raw yt-dlp error.

### Access control (optional Basic auth)

Yam speaks plain HTTP and expects TLS/access control in front of it (see
[Deploy](#deploy)). As defense-in-depth, set **both** `BASIC_AUTH_USER` and
`BASIC_AUTH_PASS` to require HTTP Basic auth on every route except `/healthz`
(kept open for container health probes). Leave either unset to disable it.

### Disk guard

Downloads are refused when free space on `MEDIA_DIR` would drop below
`MIN_FREE_SPACE_MB` (default 500 MB; set `0` to disable); the job fails with a
clear message so you can free space and retry. Current archived size and free
space are shown at `/config`.
