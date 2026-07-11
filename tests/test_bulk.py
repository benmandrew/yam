"""Bulk actions on the library grid: add-to-playlist and delete, via HTTP."""

from __future__ import annotations

from sqlmodel import Session, select

from yam.db import engine
from yam.library import create_custom_playlist
from yam.models import Playlist, PlaylistVideo, Video, VideoStatus


def _add_present_video(vid: str) -> Video:
    v = Video(id=vid, title=vid, status=VideoStatus.present)
    with Session(engine) as s:
        s.add(v)
        s.commit()
    return v


def test_bulk_add_to_playlist(client):
    playlist = create_custom_playlist("Faves")
    for vid in ("v1", "v2", "v3"):
        _add_present_video(vid)
    r = client.post(
        "/api/videos/bulk-add-to-playlist",
        data={"video_ids": ["v1", "v2", "v3"], "playlist_id": playlist.id},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "3" in r.headers["location"]
    with Session(engine) as s:
        links = s.exec(
            select(PlaylistVideo).where(PlaylistVideo.playlist_id == playlist.id)
        ).all()
    assert len(links) == 3


def test_bulk_add_to_playlist_skips_already_added(client):
    playlist = create_custom_playlist("Faves")
    for vid in ("v1", "v2", "v3"):
        _add_present_video(vid)
    with Session(engine) as s:
        s.add(PlaylistVideo(playlist_id=playlist.id, video_id="v1"))
        s.commit()
    r = client.post(
        "/api/videos/bulk-add-to-playlist",
        data={"video_ids": ["v1", "v2", "v3"], "playlist_id": playlist.id},
        follow_redirects=False,
    )
    assert r.status_code == 303
    location = r.headers["location"].replace("%20", "+")
    assert "2" in location  # added
    assert "1" in location  # skipped
    with Session(engine) as s:
        links = s.exec(
            select(PlaylistVideo).where(PlaylistVideo.playlist_id == playlist.id)
        ).all()
    assert len(links) == 3


def test_bulk_add_to_playlist_no_selection(client):
    playlist = create_custom_playlist("Faves")
    r = client.post(
        "/api/videos/bulk-add-to-playlist",
        data={"playlist_id": playlist.id},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "No+videos+selected" in r.headers["location"].replace("%20", "+")


def test_bulk_add_to_playlist_no_playlist(client):
    _add_present_video("v1")
    r = client.post(
        "/api/videos/bulk-add-to-playlist",
        data={"video_ids": ["v1"]},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "No+playlist+selected" in r.headers["location"].replace("%20", "+")


def test_bulk_delete(client):
    _add_present_video("v1")
    _add_present_video("v2")
    _add_present_video("v3")
    with Session(engine) as s:
        s.add(Playlist(id="pl1", title="PL"))
        s.add(PlaylistVideo(playlist_id="pl1", video_id="v3"))
        s.commit()
    r = client.post(
        "/api/videos/bulk-delete",
        data={"video_ids": ["v1", "v2", "v3"]},
        follow_redirects=False,
    )
    assert r.status_code == 303
    location = r.headers["location"].replace("%20", "+")
    assert "2" in location
    assert "1" in location
    with Session(engine) as s:
        assert s.get(Video, "v1") is None
        assert s.get(Video, "v2") is None
        assert s.get(Video, "v3") is not None


def test_bulk_delete_no_selection(client):
    r = client.post("/api/videos/bulk-delete", data={}, follow_redirects=False)
    assert r.status_code == 303
    assert "No+videos+selected" in r.headers["location"].replace("%20", "+")
