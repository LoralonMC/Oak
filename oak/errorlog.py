"""
Error reporting to Discord.

Attaches a logging handler to the root logger and forwards ERROR-level (and
above) records to a configured channel, so failures surface where they'll
actually be seen instead of only in container logs.

Design notes:
- ``logging.Handler.emit()`` is synchronous and may be called from a worker
  thread (``asyncio.to_thread``), so records are buffered and drained by an
  asyncio task rather than sent inline.
- Records from ``discord.*`` loggers are dropped. A failed send logs an error,
  which would otherwise be re-queued and fail again forever.
- Identical messages inside the dedupe window collapse into one report with a
  count, and each flush is capped, so an outage produces a handful of messages
  rather than hundreds.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from .bot import OakBot

logger = logging.getLogger(__name__)

# Loggers whose errors are never forwarded. Reporting a Discord failure over
# Discord is how you build an infinite loop.
_IGNORED_PREFIXES = ("discord.", "oak.errorlog")


class _BufferingErrorHandler(logging.Handler):
    """Collects error records for the manager to drain. Never raises."""

    def __init__(self, level: int, capacity: int = 200):
        super().__init__(level=level)
        self._buffer: deque[tuple[float, str, str, str]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self.dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name.startswith(_IGNORED_PREFIXES):
                return
            try:
                message = record.getMessage()
            except Exception:
                # Bad %-args. Fall back to the raw template, and if even that
                # can't be stringified, still report *something* — an error
                # reporter that silently drops the weirdest errors is useless.
                try:
                    message = str(record.msg)
                except Exception:
                    message = f"<unformattable log record from {record.name}>"
            trace = ""
            if record.exc_info:
                try:
                    trace = logging.Formatter().formatException(record.exc_info)
                except Exception:
                    trace = ""
            with self._lock:
                if len(self._buffer) == self._buffer.maxlen:
                    self.dropped += 1
                self._buffer.append((record.created, record.levelname, record.name, message + ("\n" + trace if trace else "")))
        except Exception:
            # A logging handler must never propagate. Losing one report is
            # always better than breaking the caller that was logging.
            pass

    def drain(self) -> list[tuple[float, str, str, str]]:
        with self._lock:
            items = list(self._buffer)
            self._buffer.clear()
            return items


class ErrorReporter:
    """Forwards buffered error logs to a Discord channel on an interval."""

    def __init__(
        self,
        bot: "OakBot",
        channel_id: int,
        level: int = logging.ERROR,
        flush_seconds: float = 15.0,
        max_per_flush: int = 5,
        dedupe_seconds: float = 300.0,
    ):
        self._bot = bot
        self._channel_id = channel_id
        self._flush_seconds = flush_seconds
        self._max_per_flush = max_per_flush
        self._dedupe_seconds = dedupe_seconds
        self._handler = _BufferingErrorHandler(level=level)
        self._task: asyncio.Task | None = None
        # signature -> (last_sent_monotonic, suppressed_count)
        self._recent: dict[str, tuple[float, int]] = {}

    def start(self) -> None:
        logging.getLogger().addHandler(self._handler)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="oak-error-reporter")
        logger.info(
            f"Error reporter started (channel {self._channel_id}, "
            f"level {logging.getLevelName(self._handler.level)})"
        )

    async def stop(self) -> None:
        logging.getLogger().removeHandler(self._handler)
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        logger.info("Error reporter stopped")

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._flush_seconds)
                try:
                    await self._flush()
                except Exception:
                    # Never let a reporting failure kill the loop.
                    logger.debug("Error reporter flush failed", exc_info=True)
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _signature(logger_name: str, message: str) -> str:
        """Collapse near-identical errors. First line only, so a changing
        suffix (an id, a channel number) doesn't defeat deduping."""
        return f"{logger_name}:{message.splitlines()[0][:120]}"

    async def _flush(self) -> None:
        records = self._handler.drain()
        if not records:
            return
        channel = self._bot.get_channel(self._channel_id)
        if channel is None:
            return

        now = time.monotonic()
        # Drop dedupe entries that have aged out.
        self._recent = {
            sig: value for sig, value in self._recent.items()
            if now - value[0] < self._dedupe_seconds
        }

        fresh: list[tuple[str, str, str]] = []
        for _created, levelname, name, message in records:
            sig = self._signature(name, message)
            seen = self._recent.get(sig)
            if seen and now - seen[0] < self._dedupe_seconds:
                self._recent[sig] = (seen[0], seen[1] + 1)
                continue
            self._recent[sig] = (now, 0)
            fresh.append((levelname, name, message))

        suppressed_total = sum(count for _, count in self._recent.values())
        overflow = len(fresh) - self._max_per_flush
        for levelname, name, message in fresh[: self._max_per_flush]:
            embed = discord.Embed(
                title=f"{levelname} in {name}",
                description=f"```\n{message[:1800]}\n```",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow(),
            )
            footer_bits = []
            if suppressed_total:
                footer_bits.append(f"{suppressed_total} repeat(s) suppressed")
            if self._handler.dropped:
                footer_bits.append(f"{self._handler.dropped} dropped (buffer full)")
            if footer_bits:
                embed.set_footer(text=" • ".join(footer_bits))
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                # Put nothing back: retrying a send that Discord rejected is
                # how a transient outage turns into a backlog storm.
                return

        if overflow > 0:
            try:
                await channel.send(
                    f"...and {overflow} further distinct error(s) this cycle. Check container logs."
                )
            except discord.HTTPException:
                pass
