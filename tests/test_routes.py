"""HTTP routes via TestClient: media Range streaming, dedup, health, favicon."""

from __future__ import annotations

from sqlmodel import Session, select

from yam.db import engine
from yam.main import _PAGE_SIZE, _next_in_playlist
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


def _add_video_rows(n: int, prefix: str = "v", channel: str | None = None) -> None:
    """Insert `n` present Video rows directly, with no backing file — the
    index page only needs DB rows to exercise pagination, and thumbnails are
    fetched lazily by the browser, not by these route tests."""
    with Session(engine) as s:
        for i in range(n):
            s.add(
                Video(
                    id=f"{prefix}{i:04d}",
                    title=f"{prefix} video {i:04d}",
                    channel=channel,
                    upload_date=f"202001{(i % 28) + 1:02d}",
                    status=VideoStatus.present,
                )
            )
        s.commit()


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


def test_download_dedups_in_flight_video(client):
    with Session(engine) as s:
        s.add(Job(type=JobType.video, url="x", target_id="busy"))
        s.commit()
    r = client.post(
        "/api/download",
        data={"url": "https://www.youtube.com/watch?v=busy"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "already+downloading" in loc.replace("%20", "+")
    assert "level=error" in loc
    with Session(engine) as s:
        jobs = s.exec(select(Job).where(Job.target_id == "busy")).all()
    assert len(jobs) == 1  # no duplicate job enqueued


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


# --- Videos pagination --------------------------------------------------


def _card_count(text: str) -> int:
    return text.count('href="/watch/')


def test_index_pagination_first_page(client):
    _add_video_rows(_PAGE_SIZE + 5)
    r = client.get("/")
    assert r.status_code == 200
    assert _card_count(r.text) == _PAGE_SIZE
    assert "Next" in r.text
    assert "Prev" not in r.text


def test_index_pagination_second_page(client):
    _add_video_rows(_PAGE_SIZE + 5)
    r = client.get("/?page=2")
    assert r.status_code == 200
    assert _card_count(r.text) == 5
    assert "Prev" in r.text
    assert "Next" not in r.text


def test_index_pagination_out_of_range_clamps_to_last_page(client):
    _add_video_rows(_PAGE_SIZE + 5)
    r = client.get("/?page=999")
    assert r.status_code == 200
    assert _card_count(r.text) == 5  # same as the real last page (page 2)
    assert "Prev" in r.text
    assert "Next" not in r.text


def test_index_pagination_below_range_clamps_to_first_page(client):
    _add_video_rows(_PAGE_SIZE + 5)
    for bad_page in ("0", "-1"):
        r = client.get(f"/?page={bad_page}")
        assert r.status_code == 200
        assert _card_count(r.text) == _PAGE_SIZE
        assert "Prev" not in r.text
        assert "Next" in r.text


def test_index_pagination_hidden_for_small_library(client):
    _add_video_rows(3)
    r = client.get("/")
    assert r.status_code == 200
    assert _card_count(r.text) == 3
    assert "pagination" not in r.text


def test_index_pagination_with_search_narrows_total(client):
    _add_video_rows(_PAGE_SIZE + 5, prefix="a", channel="Alpha Channel")
    _add_video_rows(3, prefix="b", channel="Bravo Channel")
    r = client.get("/?q=Bravo")
    assert r.status_code == 200
    assert _card_count(r.text) == 3
    assert "pagination" not in r.text  # narrowed total fits on one page

    r2 = client.get("/?q=Alpha&page=2")
    assert r2.status_code == 200
    assert _card_count(r2.text) == 5
    assert "Prev" in r2.text
    assert "Next" not in r2.text
