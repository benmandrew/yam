"""Database engine, SQLite pragmas, and schema initialisation."""

from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import event, inspect, text
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

log = logging.getLogger("yam.db")

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db() -> None:
    """Create data directories and tables if they don't already exist."""
    settings.ensure_dirs()
    # Import models for their side effect: registering tables on SQLModel.metadata.
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Add columns present in the models but missing from an existing SQLite
    table. `create_all` only creates whole tables, never alters them, so new
    nullable fields added over time (e.g. `Video.subtitle_path`) need this to
    reach pre-existing databases. Only nullable, default-less columns are safe
    to add this way — which is all Yam has added so far."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in have or not column.nullable:
                    continue
                col_type = column.type.compile(engine.dialect)
                conn.execute(
                    text(
                        f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'
                    )
                )
                log.info("added column %s.%s", table.name, column.name)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
