"""
Database backup utilities: WAL checkpoint, file copy, periodic scheduling.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bot import OakBot
    from .database import BranchDatabase

logger = logging.getLogger(__name__)


async def backup_database(
    db: BranchDatabase,
    backup_dir: Path | None = None,
    max_backups: int = 3,
) -> Path:
    """Checkpoint WAL and copy the .db file to a timestamped backup.

    Args:
        db: The BranchDatabase instance to back up.
        backup_dir: Directory for backups. Defaults to ``<db_parent>/backups/``.
        max_backups: Number of recent backups to keep. Older ones are pruned.

    Returns:
        Path to the new backup file.
    """
    db_path = db.path
    if backup_dir is None:
        backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Checkpoint WAL under the write lock so the .db file is up-to-date
    async with db.write_lock:
        conn = db.raw_connection()
        await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # Copy the database file in a thread to avoid blocking the event loop
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"{db_path.stem}_{stamp}.db"

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, shutil.copy2, str(db_path), str(dest))
    logger.info(f"Backup created: {dest}")

    # Prune old backups (keep most recent max_backups)
    existing = sorted(backup_dir.glob(f"{db_path.stem}_*.db"), reverse=True)
    for old in existing[max_backups:]:
        try:
            old.unlink()
            logger.debug(f"Pruned old backup: {old}")
        except OSError as e:
            logger.warning(f"Failed to prune backup {old}: {e}")

    return dest


class BackupManager:
    """Periodically backs up all loaded branch databases."""

    def __init__(self, bot: OakBot, interval_hours: float, max_backups: int = 3):
        self._bot = bot
        self._interval = interval_hours * 3600  # seconds
        self._max_backups = max_backups
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Start the periodic backup loop."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="oak-backup-manager")
            logger.info(
                f"Backup manager started (every {self._interval / 3600:.1f}h, "
                f"keep {self._max_backups})"
            )

    async def stop(self) -> None:
        """Stop the periodic backup loop and wait for it to finish.

        Awaiting the cancellation prevents a half-finished backup_all() from
        racing with branch unload / database close on shutdown.
        """
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            logger.info("Backup manager stopped")
        self._task = None

    async def _loop(self) -> None:
        """Run backups on a fixed interval."""
        try:
            while True:
                await asyncio.sleep(self._interval)
                await self.backup_all()
        except asyncio.CancelledError:
            pass

    async def backup_all(self) -> list[Path]:
        """Back up all loaded branches that have databases."""
        results: list[Path] = []
        for branch_id, instance in self._bot.loader.loaded_branches.items():
            if instance.db is None:
                continue
            try:
                path = await backup_database(instance.db, max_backups=self._max_backups)
                results.append(path)
            except Exception:
                logger.exception(f"Backup failed for branch '{branch_id}'")
        return results

    async def backup_branch(self, branch_id: str) -> Path:
        """Back up a single branch's database."""
        instance = self._bot.loader.require_branch(branch_id)
        if instance.db is None:
            raise ValueError(f"Branch '{branch_id}' has no database")
        return await backup_database(instance.db, max_backups=self._max_backups)
