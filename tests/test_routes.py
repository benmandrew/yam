"""HTTP routes via TestClient: media Range streaming, dedup, health, favicon."""

from __future__ import annotations

from sqlmodel import Session, select

from yam.db import engine
from yam.main import _next_in_playlist
from yam.models import Job, JobType, Playlist, PlaylistVideo, Video, VideoStatus

CONTENT = bytes(range(256)) * 8  # 2048 bytes of known data


def _add_present_video(vid: str, tmp_path, ext: str = "mp4") -> Video:
    path = tmp_path / f"{vid}.{ext}"
    path.write_bytes(CONTENT)
    v = Video(
        id=vid, title=vid, ext=ext, file_path=str(path), status=VideoStatus.present
    )
    with Session(engine) as s:
        s.add(v)
        s.commit()
    return v


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_favicon(client):
    r = client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_index_ok(client):
    assert client.get("/").status_code == 200


def test_media_full(client, tmp_path):
    _add_present_video("v1", tmp_path)
    r = client.get("/media/v1")
    assert r.status_code == 200
    assert r.content == CONTENT


def test_media_range_returns_206(client, tmp_path):
    _add_present_video("v2", tmp_path)
    r = client.get("/media/v2", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206
    assert r.content == CONTENT[0:10]
    assert r.headers["content-range"] == f"bytes 0-9/{len(CONTENT)}"
    assert r.headers["content-type"] == "video/mp4"


def test_media_missing_file_404(client):
    with Session(engine) as s:
        s.add(
            Video(
                id="gone",
                title="gone",
                file_path="/nope/x.mp4",
                status=VideoStatus.present,
            )
        )
        s.commit()
    assert client.get("/media/gone").status_code == 404


def test_media_unknown_id_404(client):
    assert client.get("/media/nope").status_code == 404


def test_download_dedups_present_video(client, tmp_path):
    _add_present_video("dup", tmp_path)
    r = client.post(
        "/api/download",
        data={"url": "https://www.youtube.com/watch?v=dup"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "already+archived" in r.headers["location"].replace("%20", "+")
    with Session(engine) as s:
        assert s.exec(select(Job)).all() == []  # no job enqueued


def test_download_enqueues_new_video(client):
    r = client.post(
        "/api/download",
        data={"url": "https://www.youtube.com/watch?v=fresh"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with Session(engine) as s:
        jobs = s.exec(select(Job)).all()
    assert len(jobs) == 1
    assert jobs[0].type == JobType.video
    assert jobs[0].target_id == "fresh"


def test_download_enqueues_playlist(client):
    client.post(
        "/api/download",
        data={"url": "https://www.youtube.com/playlist?list=PLxyz"},
        follow_redirects=False,
    )
    with Session(engine) as s:
        jobs = s.exec(select(Job)).all()
    assert len(jobs) == 1
    assert jobs[0].type == JobType.playlist
    assert jobs[0].target_id == "PLxyz"


def test_next_in_playlist():
    with Session(engine) as s:
        s.add(Playlist(id="PL", title="PL"))
        for vid in ["a", "b", "c"]:
            s.add(Video(id=vid, title=vid, status=VideoStatus.missing))
        s.commit()
        for pos, vid in enumerate(["a", "b", "c"]):
            s.add(PlaylistVideo(playlist_id="PL", video_id=vid, position=pos))
        s.commit()
        assert _next_in_playlist(s, "PL", "a") == "b"
        assert _next_in_playlist(s, "PL", "b") == "c"
        assert _next_in_playlist(s, "PL", "c") is None  # last entry
        assert _next_in_playlist(s, "PL", "missing") is None
        assert _next_in_playlist(s, None, "a") is None
