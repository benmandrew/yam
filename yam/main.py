"""FastAPI application entry point.

Milestone 2: real single-video downloads via a background worker, a live
downloads/progress page, and the library. Playback (watch pages) and playlists
arrive in Milestones 3–4.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from .db import engine, init_db
from .models import Job, JobType, Playlist, Video
from .urls import classify
from .worker import run_worker

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    stop = asyncio.Event()
    worker_task = asyncio.create_task(run_worker(stop))
    try:
        yield
    finally:
        stop.set()
        await worker_task


app = FastAPI(title="YAM", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with Session(engine) as session:
        videos = session.exec(select(Video).order_by(Video.downloaded_at.desc())).all()
        playlists = session.exec(select(Playlist)).all()
    return templates.TemplateResponse(
        request, "index.html", {"videos": videos, "playlists": playlists}
    )


@app.post("/api/download")
def enqueue_download(request: Request, url: str = Form(...)):
    url = url.strip()
    kind, ident = classify(url)

    if kind == "playlist":
        return _redirect_downloads("Playlist downloads arrive in Milestone 4.")

    if kind == "video" and ident and _video_present(ident):
        return _redirect_downloads("That video is already archived.")

    with Session(engine) as session:
        session.add(Job(type=JobType.video, url=url, target_id=ident))
        session.commit()
    return _redirect_downloads("Queued for download.")


@app.get("/downloads", response_class=HTMLResponse)
def downloads(request: Request, msg: str | None = None):
    return templates.TemplateResponse(request, "downloads.html", {"msg": msg})


@app.get("/api/jobs", response_class=HTMLResponse)
def jobs_partial(request: Request):
    with Session(engine) as session:
        jobs = session.exec(
            select(Job).order_by(Job.created_at.desc()).limit(100)
        ).all()
    return templates.TemplateResponse(request, "_jobs.html", {"jobs": jobs})


# --- helpers ----------------------------------------------------------------


def _redirect_downloads(msg: str) -> RedirectResponse:
    from urllib.parse import urlencode

    return RedirectResponse(f"/downloads?{urlencode({'msg': msg})}", status_code=303)


def _video_present(video_id: str) -> bool:
    with Session(engine) as session:
        return session.get(Video, video_id) is not None
