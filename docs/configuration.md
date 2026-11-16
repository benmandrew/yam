# Configuration

Yam reads all settings from environment variables. `/config` shows the effective settings, storage use, and the running yt-dlp version.

| Env var | Default | Purpose |
| --- | --- | --- |
| `MEDIA_DIR` | `/media` | Video files and thumbnails |
| `DATA_DIR` | `/data` | SQLite database and the yt-dlp overlay |
| `MAX_CONCURRENT_DOWNLOADS` | `2` | Parallel downloads |
| `DOWNLOAD_SUBTITLES` | `false` | Fetch English subtitles as WebVTT with each new video |
| `COOKIES_FILE` | — | Path to a `cookies.txt` for restricted content |
| `MIN_FREE_SPACE_MB` | `500` | Refuse downloads below this free space; `0` disables |
| `BASIC_AUTH_USER` | — | Set with `BASIC_AUTH_PASS` to require HTTP Basic auth |
| `BASIC_AUTH_PASS` | — | See above |
| `LOG_LEVEL` | `INFO` | Log level |

Basic auth covers every route except `/healthz`, which stays open for health probes.

## Cookies

If downloads fail with "Sign in to confirm you're not a bot", export a Netscape-format `cookies.txt` from a browser signed in to YouTube, put it in the data volume, and set `COOKIES_FILE=/data/cookies.txt`.

## Keeping yt-dlp current

An out-of-date yt-dlp fails YouTube downloads with HTTP 403. CI republishes the image on any day PyPI has a newer stable yt-dlp, so `docker compose pull && docker compose up -d` picks it up. To update without pulling, press "Check for update" on `/config` and restart the container. Once a pulled image carries a version at least as new, Yam ignores the installed copy.
