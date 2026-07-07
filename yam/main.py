"""FastAPI application entry point (Milestone 1 skeleton).

Provides the base layout, the library page (empty until downloads land in
Milestone 2), and a health check. Download/playback routes are stubbed and
filled in by later milestones.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from .db import engine, init_db
from .models import Playlist, Video

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="YAM", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with Session(engine) as session:
        videos = session.exec(select(Video)).all()
        playlists = session.exec(select(Playlist)).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"videos": videos, "playlists": playlists},
    )


@app.post("/api/download")
def enqueue_download() -> JSONResponse:
    # Wired up in Milestone 2 (single-video download + job worker).
    return JSONResponse(
        {"detail": "Downloading is not implemented yet (Milestone 2)."},
        status_code=501,
    )
