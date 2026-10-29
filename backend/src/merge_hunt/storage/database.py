"""
file: database.py
importable: create_engine, create_tables

create_engine(url) builds the async engine everything in storage works
through. For a SQLite file it first creates the folder the file goes in,
SQLite does not do that itself. create_tables(engine) creates the tables that
are missing and leaves existing ones alone, main.py calls both on startup.

Stores do not take the engine directly but a session maker built from it:
async_sessionmaker(engine).

description: Sets up the database connection and creates its tables.
"""

# NOTE: There is no Alembic yet. create_tables only adds missing tables, it
# never changes an existing one. Until Alembic comes a changed column means
# deleting the local database file so it is built again.

from pathlib import Path

from sqlalchemy import event, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from merge_hunt.storage.tables import Base

# --- Engine ---

# Runs on every new connection. foreign_keys is off by default in SQLite and
# without it deleting a pull request would leave its interactions behind. WAL
# lets the UI read scan progress while a scan is writing, otherwise readers
# and the writer wait for each other.
def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()

def create_engine(url: str) -> AsyncEngine:
    """Builds the async engine for the given database URL.

    For a SQLite file the folder is created when missing and every connection
    gets foreign keys and WAL switched on. An in-memory SQLite URL works too,
    the tests use it.

    Args:
        url: A SQLAlchemy URL with an async driver, for example
            sqlite+aiosqlite:///path/to/file.db.

    Returns:
        The engine. Call dispose() on it when the app shuts down.
    """
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return create_async_engine(url)
    if parsed.database and parsed.database != ":memory:":
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(url)
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    return engine

# --- Tables ---

async def create_tables(engine: AsyncEngine) -> None:
    """Creates every table that does not exist yet.

    Safe to call on each start, existing tables and their data are not touched.
    """
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
