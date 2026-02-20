"""
BranchDatabase: per-branch SQLite database with persistent connection,
WAL mode, migration tracking, and write locking.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import warnings
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

logger = logging.getLogger(__name__)

_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS _oak_migrations (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


async def _execute_script(conn: aiosqlite.Connection, sql: str) -> None:
    """Execute a multi-statement SQL script without breaking the transaction.

    Unlike ``executescript()`` which issues an implicit COMMIT first,
    this splits on ``;`` and runs each statement via ``execute()``,
    preserving the caller's transaction context.
    """
    for statement in sql.split(";"):
        statement = statement.strip()
        if statement:
            await conn.execute(statement)


@dataclass
class Migration:
    """A named migration: name + SQL to execute."""

    name: str
    sql: str


class BranchDatabase:
    """
    Per-branch SQLite database.

    One long-lived aiosqlite connection with WAL mode, foreign keys,
    and an asyncio.Lock guarding all write operations.
    """

    def __init__(self, db_path: Path):
        self._path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def initialize(self, schema: str) -> None:
        """Open the persistent connection, set pragmas, run schema."""
        if self._conn is not None:
            raise RuntimeError("Database already initialized")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._path))
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        # Create migration tracking table
        await self._conn.execute(_MIGRATIONS_TABLE)
        # Run the branch's schema (split manually to avoid implicit COMMIT)
        await _execute_script(self._conn, schema)
        await self._conn.commit()
        logger.info(f"Database initialized at {self._path}")

    async def migrate(self, migrations: list[Migration]) -> None:
        """Run named migrations that haven't been applied yet."""
        if not self._conn:
            raise RuntimeError("Database not initialized — call initialize() first")

        # Guard against duplicate migration names
        names = [m.name for m in migrations]
        if len(names) != len(set(names)):
            dupes = [n for n in names if names.count(n) > 1]
            raise ValueError(f"Duplicate migration names: {sorted(set(dupes))}")

        async with self._write_lock:
            for migration in migrations:
                cursor = await self._conn.execute(
                    "SELECT 1 FROM _oak_migrations WHERE name = ?",
                    (migration.name,),
                )
                if await cursor.fetchone():
                    continue  # already applied

                logger.info(f"Running migration: {migration.name}")
                try:
                    await _execute_script(self._conn, migration.sql)
                except sqlite3.OperationalError as e:
                    if "duplicate column name" in str(e):
                        # Column already exists (migration was applied before tracking).
                        logger.info(
                            f"Migration '{migration.name}' already applied (column exists), recording it"
                        )
                    else:
                        logger.error(
                            f"Migration '{migration.name}' failed: {e}",
                            exc_info=True,
                        )
                        raise
                except Exception as e:
                    logger.error(
                        f"Migration '{migration.name}' failed: {e}",
                        exc_info=True,
                    )
                    raise
                await self._conn.execute(
                    "INSERT INTO _oak_migrations (name) VALUES (?)",
                    (migration.name,),
                )
                await self._conn.commit()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Context manager for explicit transactions.

        Acquires the write lock, issues ``BEGIN IMMEDIATE``, yields the
        raw connection, and commits on success or rolls back on error.
        """
        if not self._conn:
            raise RuntimeError("Database not initialized")
        async with self._write_lock:
            await self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                await self._conn.commit()
            except BaseException:
                await self._conn.rollback()
                raise

    async def execute(self, query: str, params: tuple = ()) -> aiosqlite.Cursor:
        """Execute a write query (INSERT/UPDATE/DELETE) with lock."""
        if not self._conn:
            raise RuntimeError("Database not initialized")
        async with self._write_lock:
            cursor = await self._conn.execute(query, params)
            await self._conn.commit()
            return cursor

    async def fetchone(self, query: str, params: tuple = ()) -> aiosqlite.Row | None:
        """Execute a read query and return one row."""
        if not self._conn:
            raise RuntimeError("Database not initialized")
        cursor = await self._conn.execute(query, params)
        return await cursor.fetchone()

    async def fetchall(self, query: str, params: tuple = ()) -> list[aiosqlite.Row]:
        """Execute a read query and return all rows."""
        if not self._conn:
            raise RuntimeError("Database not initialized")
        cursor = await self._conn.execute(query, params)
        return await cursor.fetchall()

    @property
    def write_lock(self) -> asyncio.Lock:
        """Return the write lock for advanced use."""
        return self._write_lock

    def raw_connection(self) -> aiosqlite.Connection:
        """Return the raw persistent connection for advanced use.

        Warning: callers using this connection directly bypass the write lock.
        Use ``write_lock`` to serialise writes when operating on the raw
        connection.
        """
        if not self._conn:
            raise RuntimeError("Database not initialized")
        return self._conn

    def connect(self) -> aiosqlite.Connection:
        """Return the raw persistent connection.

        .. deprecated:: Use ``raw_connection()`` instead.
        """
        warnings.warn(
            "BranchDatabase.connect() is deprecated, use raw_connection() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.raw_connection()

    @property
    def path(self) -> Path:
        """Return the database file path."""
        return self._path

    async def close(self) -> None:
        """Close the persistent connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info(f"Database closed: {self._path}")
