"""Small pure helpers in main.py and downloader.py."""

from __future__ import annotations

import pytest

from yam.downloader import friendly_error
from yam.main import _fmt_duration, _fmt_ytdate, _mime_for


@pytest.mark.parametrize(
    ("ext", "mime"),
    [
        ("mp4", "video/mp4"),
        ("m4v", "video/mp4"),
        ("webm", "video/webm"),
        ("mkv", "video/x-matroska"),
        ("MP4", "video/mp4"),
        (None, "application/octet-stream"),
        ("avi", "application/octet-stream"),
    ],
)
def test_mime_for(ext, mime):
    assert _mime_for(ext) == mime


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, ""), (None, ""), (65, "1:05"), (3661, "1:01:01"), (600, "10:00")],
)
def test_fmt_duration(seconds, text):
    assert _fmt_duration(seconds) == text


@pytest.mark.parametrize(
    ("value", "text"),
    [("20240102", "2024-01-02"), ("", ""), (None, ""), ("bad", "bad")],
)
def test_fmt_ytdate(value, text):
    assert _fmt_ytdate(value) == text


def test_friendly_error_bot_check():
    msg = friendly_error(Exception("Sign in to confirm you're not a bot"))
    assert "cookies.txt" in msg
    assert "COOKIES_FILE" in msg


def test_friendly_error_passthrough():
    assert friendly_error(Exception("boom")) == "boom"
