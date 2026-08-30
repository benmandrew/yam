"""Tests for the yt-dlp overlay under DATA_DIR.

Offline throughout: PyPI lookups and the pip subprocess are both monkeypatched,
so nothing here reaches the network or installs anything.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from yam import ytdlp_update
from yam.ytdlp_update import UpdateError, version_key


@pytest.fixture
def overlay(tmp_path, monkeypatch) -> Path:
    """Point the overlay at a per-test directory. The real one lives under the
    shared temp DATA_DIR, so tests would otherwise leak into each other."""
    d = tmp_path / "ytdlp"
    monkeypatch.setattr(ytdlp_update, "overlay_dir", lambda: d)
    return d


def install(overlay: Path, version: str) -> None:
    """Fake an installed wheel by creating just the .dist-info directory that
    ``overlay_version`` reads the version out of."""
    overlay.mkdir(parents=True, exist_ok=True)
    (overlay / f"yt_dlp-{version}.dist-info").mkdir()


# --- version ordering -------------------------------------------------------


def test_version_key_orders_releases():
    assert version_key("2026.7.4") < version_key("2026.8.19")
    assert version_key("2026.8.19") < version_key("2026.8.29")


def test_version_key_ignores_zero_padding():
    assert version_key("2026.08.19") == version_key("2026.8.19")


def test_version_key_sorts_nightlies_after_their_release():
    assert version_key("2026.8.29") < version_key("2026.8.29.232711.dev0")
    assert version_key("2026.8.29.232711.dev0") < version_key("2026.8.30")


def test_version_key_survives_unparseable_input():
    assert version_key("not-a-version") == (0,)


# --- overlay discovery ------------------------------------------------------


def test_overlay_version_none_when_absent(overlay):
    assert ytdlp_update.overlay_version() is None


def test_overlay_version_reads_dist_info(overlay):
    install(overlay, "2026.8.19")
    assert ytdlp_update.overlay_version() == "2026.8.19"


# --- path activation --------------------------------------------------------


@pytest.fixture(autouse=True)
def restore_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before


def test_newer_overlay_goes_on_the_path(overlay, monkeypatch):
    install(overlay, "2026.8.19")
    monkeypatch.setattr(ytdlp_update, "baked_version", lambda: "2026.7.4")
    ytdlp_update.ensure_overlay_on_path()
    assert sys.path[0] == str(overlay)


def test_older_overlay_is_ignored(overlay, monkeypatch):
    install(overlay, "2026.7.4")
    monkeypatch.setattr(ytdlp_update, "baked_version", lambda: "2026.8.19")
    ytdlp_update.ensure_overlay_on_path()
    assert str(overlay) not in sys.path


def test_equal_overlay_is_ignored(overlay, monkeypatch):
    """A rebuilt image that catches up with the overlay should take over, so the
    overlay must lose ties rather than shadow an identical copy forever."""
    install(overlay, "2026.8.19")
    monkeypatch.setattr(ytdlp_update, "baked_version", lambda: "2026.8.19")
    ytdlp_update.ensure_overlay_on_path()
    assert str(overlay) not in sys.path


def test_missing_overlay_is_a_no_op(overlay):
    before = list(sys.path)
    ytdlp_update.ensure_overlay_on_path()
    assert sys.path == before


def test_broken_overlay_does_not_raise(overlay, monkeypatch):
    def boom():
        raise RuntimeError("unreadable")

    monkeypatch.setattr(ytdlp_update, "overlay_version", boom)
    ytdlp_update.ensure_overlay_on_path()  # swallowed, so the app still boots
    assert str(overlay) not in sys.path


# --- update -----------------------------------------------------------------


@pytest.fixture
def no_pip(monkeypatch):
    """Fail loudly if a test lets the real pip run."""

    def forbidden(*args, **kwargs):
        raise AssertionError("pip should not have been invoked")

    monkeypatch.setattr(subprocess, "run", forbidden)


def test_update_skips_pip_when_already_current(overlay, monkeypatch, no_pip):
    monkeypatch.setattr(ytdlp_update, "running_version", lambda: "2026.8.19")
    monkeypatch.setattr(ytdlp_update, "latest_version", lambda: "2026.8.19")
    assert ytdlp_update.update() == ("2026.8.19", "2026.8.19")


def test_update_skips_pip_when_running_ahead_of_stable(overlay, monkeypatch, no_pip):
    """A nightly, or an image built from a release PyPI has since yanked."""
    monkeypatch.setattr(
        ytdlp_update, "running_version", lambda: "2026.8.29.232711.dev0"
    )
    monkeypatch.setattr(ytdlp_update, "latest_version", lambda: "2026.8.19")
    running, latest = ytdlp_update.update()
    assert running == latest


def test_update_installs_the_pinned_version(overlay, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ytdlp_update, "running_version", lambda: "2026.7.4")
    monkeypatch.setattr(ytdlp_update, "latest_version", lambda: "2026.8.19")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert ytdlp_update.update() == ("2026.7.4", "2026.8.19")
    (cmd,) = calls
    assert "yt-dlp==2026.8.19" in cmd
    assert "--target" in cmd and str(overlay) in cmd
    assert overlay.is_dir()


def test_update_reports_pip_failure(overlay, monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="ERROR: No matching distribution found\n"
        )

    monkeypatch.setattr(ytdlp_update, "running_version", lambda: "2026.7.4")
    monkeypatch.setattr(ytdlp_update, "latest_version", lambda: "2026.8.19")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(UpdateError, match="No matching distribution"):
        ytdlp_update.update()


def test_update_reports_a_missing_pip(overlay, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(ytdlp_update, "running_version", lambda: "2026.7.4")
    monkeypatch.setattr(ytdlp_update, "latest_version", lambda: "2026.8.19")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(UpdateError, match="Could not run pip"):
        ytdlp_update.update()


# --- routes -----------------------------------------------------------------


def flash(response) -> str:
    return parse_qs(urlparse(response.headers["location"]).query)["msg"][0]


def test_config_page_shows_the_running_version(client):
    r = client.get("/config")
    assert r.status_code == 200
    assert ytdlp_update.running_version() in r.text
    assert "bundled with the image" in r.text


def test_config_page_names_the_overlay_when_active(client, monkeypatch):
    monkeypatch.setattr(ytdlp_update, "overlay_version", lambda: "2026.8.19")
    r = client.get("/config")
    assert "data-directory overlay" in r.text


def test_update_route_reports_an_install(client, monkeypatch):
    monkeypatch.setattr(ytdlp_update, "update", lambda: ("2026.7.4", "2026.8.19"))
    r = client.post("/api/ytdlp/update", follow_redirects=False)
    assert r.status_code == 303
    assert "2026.8.19 installed (was 2026.7.4)" in flash(r)


def test_update_route_reports_no_change(client, monkeypatch):
    monkeypatch.setattr(ytdlp_update, "update", lambda: ("2026.8.19", "2026.8.19"))
    r = client.post("/api/ytdlp/update", follow_redirects=False)
    assert r.status_code == 303
    assert "already current" in flash(r)


def test_update_route_surfaces_failure_as_an_error_flash(client, monkeypatch):
    def boom():
        raise UpdateError("Could not reach PyPI to check for updates: timed out")

    monkeypatch.setattr(ytdlp_update, "update", boom)
    r = client.post("/api/ytdlp/update", follow_redirects=False)
    assert r.status_code == 303
    query = parse_qs(urlparse(r.headers["location"]).query)
    assert query["level"] == ["error"]
    assert "Could not reach PyPI" in query["msg"][0]
