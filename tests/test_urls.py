"""URL classification (no network)."""

from __future__ import annotations

import pytest

from yam.urls import classify


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", ("video", "dQw4w9WgXcQ")),
        ("https://youtu.be/dQw4w9WgXcQ", ("video", "dQw4w9WgXcQ")),
        ("https://youtu.be/dQw4w9WgXcQ?t=42", ("video", "dQw4w9WgXcQ")),
        ("https://www.youtube.com/shorts/abc123DEF45", ("video", "abc123DEF45")),
        ("https://www.youtube.com/embed/abc123DEF45", ("video", "abc123DEF45")),
        ("https://www.youtube.com/playlist?list=PL123", ("playlist", "PL123")),
        # watch?v=…&list=… downloads the single video, not the playlist.
        (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123",
            ("video", "dQw4w9WgXcQ"),
        ),
        # list-only /watch is treated as a playlist.
        ("https://www.youtube.com/watch?list=PL123", ("playlist", "PL123")),
        ("https://www.youtube-nocookie.com/watch?v=xyz", ("video", "xyz")),
        ("https://example.com/watch?v=xyz", ("unknown", None)),
        ("not a url at all", ("unknown", None)),
    ],
)
def test_classify(url, expected):
    assert classify(url) == expected
