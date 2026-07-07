# YAM — Yet Another (YouTube) Media archiver

Self-hosted app to archive YouTube **videos and playlists** by link, store them
locally, and play them back through one web interface. See [PLAN.md](./PLAN.md)
for the full design and roadmap.

> **Status:** Milestone 1 (skeleton). Library page, health check, DB schema, and
> the Nix/Docker/Tailscale scaffolding are in place. Downloading and playback
> arrive in Milestones 2–4.

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

## Build the container image

The image is built by Nix from the same dependency set (no Dockerfile). Because
`dockerTools` produces a **Linux** image, build it on Linux (or with a Linux
remote builder — building `.#docker` on macOS will fail):

```sh
nix build .#docker
docker load < result          # loads yam:latest
```

## Deploy (Tailscale-only)

The app is exposed **only on your tailnet** via a Tailscale sidecar — nothing is
published to the host or LAN.

```sh
cp .env.example .env          # add your TS_AUTHKEY
docker compose up -d
```

It appears on your tailnet as `yam` — reachable at `https://yam.<tailnet>.ts.net`
(HTTPS via `tailscale serve`; Tailscale Funnel is intentionally left off).

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `MEDIA_DIR` | `/media` | Where video files/thumbnails are stored |
| `DATA_DIR` | `/data` | SQLite DB location |
| `MAX_CONCURRENT_DOWNLOADS` | `2` | Parallel download workers |
| `DOWNLOAD_SUBTITLES` | `false` | Fetch subtitles alongside videos |
| `COOKIES_FILE` | — | Path to cookies.txt for restricted content |

The app listens on port **8080** inside the container/package. To use a different
port in local dev, pass it to uvicorn: `uvicorn yam.main:app --port 9000`.
