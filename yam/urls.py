"""Lightweight YouTube URL classification (no network calls).

Used to decide, before touching the network, whether a pasted URL is a single
video or a playlist, and to extract the id for dedup.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def classify(url: str) -> tuple[str, str | None]:
    """Return ``(kind, id)`` where kind is ``"video"``, ``"playlist"``, or
    ``"unknown"``. ``id`` is the video id or playlist id when determinable.

    A ``watch?v=…&list=…`` URL is treated as a *video* (download just that
    video); only list-only URLs are playlists.
    """
    u = urlparse(url)
    host = u.netloc.lower()
    qs = parse_qs(u.query)
    path = u.path

    if "youtu.be" in host:
        vid = path.lstrip("/").split("/")[0]
        return ("video", vid) if vid else ("unknown", None)

    if "youtube.com" in host or "youtube-nocookie.com" in host:
        if path == "/playlist":
            return ("playlist", qs.get("list", [None])[0])
        if path.startswith("/shorts/"):
            parts = path.split("/")
            return ("video", parts[2]) if len(parts) > 2 else ("unknown", None)
        if path.startswith("/embed/"):
            parts = path.split("/")
            return ("video", parts[2]) if len(parts) > 2 else ("unknown", None)
        if path == "/watch":
            if "v" in qs:
                return ("video", qs["v"][0])
            if "list" in qs:
                return ("playlist", qs["list"][0])

    return ("unknown", None)
