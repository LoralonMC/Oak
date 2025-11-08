"""
Database utility functions for branches.

Each branch should manage its own database file in its folder.
This module provides helper functions for database operations.
"""
import aiosqlite
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def init_branch_database(db_path: str, schema: str, branch_name: str = "Branch") -> None:
    """
    Initialize a branch's database with the provided schema.

    Args:
        db_path: Path to the database file (e.g., "branches/suggestions/data.db")
        schema: SQL schema to execute (CREATE TABLE statements)
        branch_name: Name of the branch (for logging)
    """
    try:
        # Ensure parent directory exists
        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(db_path) as db:
            # Enable foreign key constraints
            await db.execute("PRAGMA foreign_keys = ON")

            # Execute schema (can be multiple statements)
            await db.executescript(schema)
            await db.commit()
            logger.info(f"{branch_name} database initialized at {db_path}")
    except Exception as e:
        logger.error(f"Failed to initialize {branch_name} database: {e}")
        raise


async def get_db_connection(db_path: str) -> aiosqlite.Connection:
    """
    Get a database connection for a branch with foreign keys enabled.

    Usage:
        async with await get_db_connection(self.db_path) as db:
            cursor = await db.execute("SELECT * FROM table")
            ...

    Args:
        db_path: Path to the database file

    Returns:
        aiosqlite.Connection with foreign keys enabled
    """
    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA foreign_keys = ON")
    return db
