# Yam — project guide for Claude

Self-hosted app to archive YouTube videos and playlists by link and play them back through one web UI. `README.md` covers running and deploying; `docs/configuration.md` covers settings.
## Layout

- `yam/main.py` — FastAPI app, routes, lifespan. `worker.py` runs download jobs; `downloader.py` wraps yt-dlp; `library.py` handles deletes; `models.py` holds the SQLModel tables (Video, Playlist, PlaylistVideo, Job).
- `yam/config.py` — env-only settings, plain dataclass.
- `tests/` — offline pytest suite.
- `.github/workflows/` — `ci.yml` (checks, image build, Docker Hub push) and `ytdlp-rebuild.yml` (daily trigger).

## Gotchas

- Models have no ORM `Relationship()`. Never insert a PlaylistVideo in the same flush as the Playlist or Video it references; commit the parents first.
- Use `templates.TemplateResponse(request, name, context)`. The old `(name, {"request": ...})` form raises `TypeError: unhashable type: 'dict'` on the pinned Starlette.
- New model columns must be nullable with no default so `db.py::_add_missing_columns` can migrate existing databases.
- `ensure_overlay_on_path()` must stay in `yam/__init__.py`, the only point that runs before `yam.downloader` imports `yt_dlp`.
- Tests stay offline. `conftest.py` sets a temp `DATA_DIR`/`MEDIA_DIR` before any `yam.*` import, and the `client` fixture builds a TestClient without the lifespan, so the worker never starts.
- The flake sees only git-tracked files. `git add` new files before `nix build` or `nix flake check`.

## Dependencies

Two sources, bumped together: the flake's `pyDeps` for dev, and `requirements.txt` for the `Dockerfile`. ffmpeg is a runtime dep (flake `runtimeDeps`, `apt-get` in the Dockerfile). Test-only deps live in the flake's `testEnv`.

## Commands

Run everything inside `nix develop`, which sets `MEDIA_DIR`/`DATA_DIR` to `./.local/*`.

| Task | Command |
| --- | --- |
| Run (hot reload) | `uvicorn yam.main:app --reload --port 8080` |
| Test | `pytest` |
| Lint / format | `ruff check .` / `ruff format .` / `nix fmt` |
| Full check (CI) | `nix flake check` |
| Build | `nix build .#yam` / `docker build -t yam:latest .` |
