# Yam

Self-hosted app that archives YouTube videos and playlists by link and plays them back through one web interface. Downloads use yt-dlp and prefer H.264/AAC mp4 at up to 1080p, so they play in any browser.

## Develop

```sh
nix develop
uvicorn yam.main:app --reload --port 8080
```

## Deploy

CI pushes a multi-arch image to Docker Hub as `<DOCKERHUB_USERNAME>/yam` on every push to `main`, using the repo secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`. `docker-compose.yml` runs it on port 8080 over plain HTTP, so put TLS in front of it, for example with `tailscale serve --bg --https=8449 http://127.0.0.1:8080`.

Settings, cookies, and yt-dlp updates are covered in [docs/configuration.md](docs/configuration.md).
