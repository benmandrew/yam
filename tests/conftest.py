"""Shared test fixtures.

`yam.config.settings` and `yam.db.engine` are module-level singletons bound at
import time, so the test DB/media locations must be set in the environment
*before* any `yam.*` module is imported. This file runs first (pytest imports
conftest before collecting tests), so we set the env here, then import.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="yam-tests-"))
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["MEDIA_DIR"] = str(_TMP / "media")
os.environ["MIN_FREE_SPACE_MB"] = "0"  # disable the disk guard in tests
os.environ.pop("BASIC_AUTH_USER", None)
os.environ.pop("BASIC_AUTH_PASS", None)

from sqlmodel import Session, SQLModel  # noqa: E402

from yam.config import settings  # noqa: E402
from yam.db import engine, init_db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    """Give every test an empty schema on the shared temp DB."""
    settings.ensure_dirs()  # create the data dir before any connection
    SQLModel.metadata.drop_all(engine)
    init_db()
    yield
    SQLModel.metadata.drop_all(engine)


@pytest.fixture
def session():
    with Session(engine) as s:
        yield s


@pytest.fixture
def client():
    """A TestClient that does NOT run the app lifespan, so the background
    download worker never starts (and never touches the network)."""
    from fastapi.testclient import TestClient

    from yam.main import app

    return TestClient(app)


@pytest.fixture
def media_dir() -> Path:
    return settings.media_dir
