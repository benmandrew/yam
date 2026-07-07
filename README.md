# Yam — a self-hosted YouTube video & playlist archiver

Self-hosted app to archive YouTube **videos and playlists** by link, store them
locally, and play them back through one web interface. See [PLAN.md](./PLAN.md)
for the full design and roadmap.

> **Status:** Milestones 1–6 done — downloads, playback, playlists, library/job
> management, and playlist sync. See [PLAN.md](./PLAN.md) for the remaining roadmap.

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

The app listens on port **8080** inside the container/package. To use a different
port in local dev, pass it to uvicorn: `uvicorn yam.main:app --port 9000`.
