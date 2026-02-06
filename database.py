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
            # Enable WAL mode for better concurrent read/write performance
            # (persists across connections once set)
            await db.execute("PRAGMA journal_mode = WAL")

            # Execute schema (can be multiple statements)
            await db.executescript(schema)
            logger.info(f"{branch_name} database initialized at {db_path}")
    except Exception as e:
        logger.error(f"Failed to initialize {branch_name} database: {e}")
        raise
