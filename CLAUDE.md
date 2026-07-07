# YAM — project guide for Claude

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
shown as "pending" in the playlist). Remaining: management/polish (PLAN.md M5–6).

## Structure

```
flake.nix              Nix flake — the single source of dependency truth
yam/
  config.py            Env-driven settings (plain dataclass, no pydantic-settings)
  db.py                SQLite engine, WAL/foreign-key pragmas, init_db()
  models.py            SQLModel tables: Video, Playlist, PlaylistVideo, Job
  main.py              FastAPI app: lifespan init, routes, static/template mounts
  templates/           Jinja2 (base.html + index.html); htmx planned for M2+
  static/style.css     Dark library UI
docker-compose.yml     Deploy: yam + Tailscale sidecar (tailnet-only)
tailscale/serve.json   HTTPS via `tailscale serve` (Funnel stays off)
PLAN.md                Architecture, data model, milestones
```

## Conventions

- **Nix is the only dependency manager.** No `pip`, no `requirements.txt`, no
  Dockerfile. Add a Python/runtime dep by editing `pythonEnv`/`runtimeDeps` in
  `flake.nix` (packages come from nixpkgs).
- **Videos are stored once, keyed by YouTube id**; playlists reference them
  through the `playlist_video` link table (a video in N playlists = one file).
- **Highest quality, no re-encode** (`bv*+ba/b`, merged to a browser-playable
  container `webm/mp4/mkv`) — see `PLAN.md`.
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
| Build container image (Linux only) | `nix build .#docker && docker load < result` |
| Lint | `ruff check .` |
| Format Python | `ruff format .` |
| Format Nix | `nix fmt` (nixpkgs-fmt) |
| Validate whole flake | `nix flake check` |

In the devShell, `MEDIA_DIR`/`DATA_DIR` default to `./.local/*`.

**Note:** the Nix flake only sees git-tracked files — `git add` new files before
`nix build`/`nix flake check` or they won't be in the build. The container image
is a Linux image; building `.#docker` on macOS fails without a Linux builder.
