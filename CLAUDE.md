# Yam — project guide for Claude

Self-hosted app to archive YouTube **videos and playlists** by link, store them
locally, and play them back through one web UI. Replaces a heavyweight
TubeArchivist install with something minimal.

**Design & roadmap live in `PLAN.md`** — read it for architecture decisions,
data model, milestones, and locked choices. Update `PLAN.md` when scope changes.

## Status

Milestones 1–4 (MVP) are done: Nix/Docker/Tailscale scaffolding + DB schema (M1);
single-video downloads via the background worker with a live `/downloads` page
(M2); playback — `/media/{id}` Range streaming, `/watch/{id}` native `<video>`
player, thumbnail library grid (M3); and playlists — enumeration, ordered
`playlist_video` links, per-entry child jobs with dedup across playlists,
`/playlist/{id}` view, and manual "Next ▸" playback (M4). Not-yet-downloaded
playlist entries are stored as `missing` Video rows (hidden from the library,
shown as "pending" in the playlist).

M5 (library & job management) is also done: delete video (`yam/library.py`, refused
while any playlist references it) and delete playlist (full-deletes orphaned videos),
retry/clear on `/downloads`, and library search + sort.

M6 (playlist sync & polish) is done: `POST /api/playlists/{id}/sync` re-enumerates and
prunes removed links (files kept), retry-pending re-queues missing entries, playlist
cover thumbnails (`/playlist/{id}/thumbnail` = first present entry), and `/downloads`
nests child video jobs under their parent playlist job. Remaining: PLAN.md M7–8.

Note: models have no ORM `Relationship()`, so never insert a PlaylistVideo in the same
flush as the Playlist/Video it references — commit the parents first (the worker does).

## Structure

```
flake.nix              Nix flake — dev shell + `nix run` (dev dependency source)
Dockerfile             Production image build (python:slim + ffmpeg + pip)
requirements.txt       Image Python deps (keep ~in sync with flake pythonEnv)
yam/
  config.py            Env-driven settings (plain dataclass, no pydantic-settings)
  db.py                SQLite engine, WAL/foreign-key pragmas, init_db()
  models.py            SQLModel tables: Video, Playlist, PlaylistVideo, Job
  main.py              FastAPI app: lifespan init, routes, static/template mounts
  templates/           Jinja2 (base.html + index.html); htmx planned for M2+
  static/style.css     Dark library UI
docker-compose.yml           Deploy: single container, publishes 8080; front with
                             host TLS (e.g. host-level `tailscale serve`)
PLAN.md                Architecture, data model, milestones
```

## Conventions

- **Two dependency sources, kept in sync:** the Nix flake `pythonEnv` for dev
  (`nix develop` / `nix run`), and `requirements.txt` for the production image
  (`Dockerfile`). Bump both together when changing deps. ffmpeg is a runtime dep
  (flake `runtimeDeps`; `apt-get` in the Dockerfile).
- **Videos are stored once, keyed by YouTube id**; playlists reference them
  through the `playlist_video` link table (a video in N playlists = one file).
- **Prefer H.264/AAC mp4, no re-encode** (for Safari/iOS + universal playback;
  ~1080p cap) — see `PLAN.md`. Fallbacks may be webm/mkv.
- **Starlette gotcha:** use the modern `templates.TemplateResponse(request, name,
  context)` signature. The old `(name, {"request": ...})` form raises
  `TypeError: unhashable type: 'dict'` on the pinned Starlette.
- Config is env-only (`config.py`); the container/package listens on **8080**.

## Commands

All tooling is provided by the flake; enter the devShell first:

```sh
nix develop                                  # devShell (python env + ffmpeg + ruff + nixpkgs-fmt)
```

| Task | Command |
| --- | --- |
| Run (dev, hot reload) | `uvicorn yam.main:app --reload --port 8080` |
| Run packaged app | `nix run .#yam` (honors `MEDIA_DIR`/`DATA_DIR`) |
| Build app | `nix build .#yam` |
| Build container image | `docker build -t yam:latest .` |
| Lint | `ruff check .` |
| Format Python | `ruff format .` |
| Format Nix | `nix fmt` (nixpkgs-fmt) |
| Validate whole flake | `nix flake check` |

In the devShell, `MEDIA_DIR`/`DATA_DIR` default to `./.local/*`.

**Note:** the Nix flake only sees git-tracked files — `git add` new files before
`nix build`/`nix flake check` or they won't be in the build.
