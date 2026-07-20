"""Thumbnail downscaling (yam/thumbnails.py). Uses ffmpeg to synthesize test
images, so these run only where ffmpeg is on PATH (dev shell + `nix flake check`)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from yam.thumbnails import _probe_width, resize_thumbnail

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


def _make_jpg(path, width, height) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={width}x{height}:duration=1:rate=1",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def test_resize_downscales_large_thumbnail(tmp_path):
    thumb = tmp_path / "big.jpg"
    _make_jpg(thumb, 1280, 720)

    assert resize_thumbnail(thumb) is True
    assert _probe_width(thumb) == 480
    # Aspect ratio preserved (720/1280 * 480 == 270).
    assert thumb.stat().st_size > 0


def test_resize_is_idempotent(tmp_path):
    thumb = tmp_path / "big.jpg"
    _make_jpg(thumb, 1280, 720)

    assert resize_thumbnail(thumb) is True
    # Second pass sees a 480px image and leaves it untouched.
    assert resize_thumbnail(thumb) is False


def test_resize_leaves_small_thumbnail_untouched(tmp_path):
    thumb = tmp_path / "small.jpg"
    _make_jpg(thumb, 320, 180)
    before = thumb.read_bytes()

    assert resize_thumbnail(thumb) is False
    assert thumb.read_bytes() == before


def test_resize_missing_file_is_noop(tmp_path):
    assert resize_thumbnail(tmp_path / "nope.jpg") is False
