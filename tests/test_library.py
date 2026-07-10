"""Library CRUD: video/playlist deletion and custom playlist management."""

from __future__ import annotations

from sqlmodel import Session, select

from yam.db import engine
from yam.library import (
    add_video_to_playlist,
    create_custom_playlist,
    delete_playlist,
    remove_video_from_playlist,
)
from yam.models import Playlist, PlaylistOrigin, PlaylistVideo, Video, VideoStatus


def _add_present_video(vid: str) -> Video:
    v = Video(id=vid, title=vid, status=VideoStatus.present)
    with Session(engine) as s:
        s.add(v)
        s.commit()
    return v


def test_create_custom_playlist_sets_origin_and_id():
    playlist = create_custom_playlist("My Mix")
    assert playlist.origin == PlaylistOrigin.custom
    assert playlist.id
    with Session(engine) as s:
        row = s.get(Playlist, playlist.id)
    assert row is not None
    assert row.title == "My Mix"


def test_add_video_to_playlist_success():
    playlist = create_custom_playlist("Faves")
    _add_present_video("v1")
    ok, msg = add_video_to_playlist(playlist.id, "v1")
    assert ok is True
    assert "Faves" in msg
    with Session(engine) as s:
        link = s.get(PlaylistVideo, (playlist.id, "v1"))
    assert link is not None


def test_add_video_to_playlist_fails_playlist_not_found():
    _add_present_video("v1")
    ok, _msg = add_video_to_playlist("nope", "v1")
    assert ok is False


def test_add_video_to_playlist_fails_non_custom_origin():
    with Session(engine) as s:
        s.add(Playlist(id="yt1", title="YT playlist"))  # no origin -> youtube-style
        s.commit()
    _add_present_video("v1")
    ok, _msg = add_video_to_playlist("yt1", "v1")
    assert ok is False


def test_add_video_to_playlist_fails_video_missing_status():
    playlist = create_custom_playlist("Faves")
    with Session(engine) as s:
        s.add(Video(id="v1", title="v1", status=VideoStatus.missing))
        s.commit()
    ok, _msg = add_video_to_playlist(playlist.id, "v1")
    assert ok is False


def test_add_video_to_playlist_fails_video_nonexistent():
    playlist = create_custom_playlist("Faves")
    ok, _msg = add_video_to_playlist(playlist.id, "nope")
    assert ok is False


def test_add_video_to_playlist_fails_duplicate():
    playlist = create_custom_playlist("Faves")
    _add_present_video("v1")
    ok1, _ = add_video_to_playlist(playlist.id, "v1")
    assert ok1 is True
    ok2, msg2 = add_video_to_playlist(playlist.id, "v1")
    assert ok2 is False
    assert "Already" in msg2


def test_remove_video_from_playlist_leaves_video_untouched():
    playlist = create_custom_playlist("Faves")
    _add_present_video("v1")
    add_video_to_playlist(playlist.id, "v1")
    ok, _msg = remove_video_from_playlist(playlist.id, "v1")
    assert ok is True
    with Session(engine) as s:
        link = s.get(PlaylistVideo, (playlist.id, "v1"))
        video = s.get(Video, "v1")
    assert link is None
    assert video is not None


def test_remove_video_from_playlist_fails_non_custom():
    with Session(engine) as s:
        s.add(Playlist(id="yt1", title="YT playlist"))
        s.commit()
    _add_present_video("v1")
    with Session(engine) as s:
        s.add(PlaylistVideo(playlist_id="yt1", video_id="v1"))
        s.commit()
    ok, _msg = remove_video_from_playlist("yt1", "v1")
    assert ok is False


def test_remove_video_from_playlist_fails_missing_link():
    playlist = create_custom_playlist("Faves")
    _add_present_video("v1")
    ok, _msg = remove_video_from_playlist(playlist.id, "v1")
    assert ok is False


def test_delete_custom_playlist_keeps_video():
    playlist = create_custom_playlist("Faves")
    _add_present_video("v1")
    add_video_to_playlist(playlist.id, "v1")
    ok, msg = delete_playlist(playlist.id)
    assert ok is True
    assert "orphaned" not in msg
    with Session(engine) as s:
        video = s.get(Video, "v1")
        row = s.get(Playlist, playlist.id)
        links = s.exec(
            select(PlaylistVideo).where(PlaylistVideo.playlist_id == playlist.id)
        ).all()
    assert video is not None
    assert row is None
    assert links == []


def test_delete_youtube_playlist_orphans_video():
    with Session(engine) as s:
        s.add(Playlist(id="yt1", title="YT playlist", origin=PlaylistOrigin.youtube))
        s.commit()
    _add_present_video("v1")
    with Session(engine) as s:
        s.add(PlaylistVideo(playlist_id="yt1", video_id="v1"))
        s.commit()
    ok, msg = delete_playlist("yt1")
    assert ok is True
    assert "orphaned" in msg
    with Session(engine) as s:
        video = s.get(Video, "v1")
    assert video is None


def test_create_playlist_route_via_http(client):
    r = client.post(
        "/api/playlists", data={"title": "HTTP Mix"}, follow_redirects=False
    )
    assert r.status_code == 303
    location = r.headers["location"]
    r2 = client.get(location)
    assert r2.status_code == 200
    with Session(engine) as s:
        rows = s.exec(
            select(Playlist).where(Playlist.origin == PlaylistOrigin.custom)
        ).all()
    assert len(rows) == 1
    assert rows[0].title == "HTTP Mix"
