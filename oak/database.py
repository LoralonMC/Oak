"""
BranchDatabase: per-branch SQLite database with persistent connection,
WAL mode, migration tracking, and write locking.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS _oak_migrations (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


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
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._path))
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        # Create migration tracking table
        await self._conn.execute(_MIGRATIONS_TABLE)
        # Run the branch's schema
        await self._conn.executescript(schema)
        await self._conn.commit()
        logger.info(f"Database initialized at {self._path}")

    async def migrate(self, migrations: list[Migration]) -> None:
        """Run named migrations that haven't been applied yet."""
        if not self._conn:
            raise RuntimeError("Database not initialized — call initialize() first")

        async with self._write_lock:
            for migration in migrations:
                cursor = await self._conn.execute(
                    "SELECT 1 FROM _oak_migrations WHERE name = ?",
                    (migration.name,),
                )
                if await cursor.fetchone():
                    continue  # already applied

                logger.info(f"Running migration: {migration.name}")
                await self._conn.executescript(migration.sql)
                await self._conn.execute(
                    "INSERT INTO _oak_migrations (name) VALUES (?)",
                    (migration.name,),
                )
                await self._conn.commit()

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

    def connect(self) -> aiosqlite.Connection:
        """Return the raw persistent connection for advanced use."""
        if not self._conn:
            raise RuntimeError("Database not initialized")
        return self._conn

    async def close(self) -> None:
        """Close the persistent connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info(f"Database closed: {self._path}")
