"""Shared utility functions."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from .constants import EMBED_FIELD_VALUE_MAX

if TYPE_CHECKING:
    import discord

logger = logging.getLogger(__name__)


def sanitize_text(text: str, max_length: int = 2000) -> str:
    """Sanitize user input text: truncate, remove null bytes, strip whitespace."""
    if not text:
        return ""
    text = text[:max_length]
    text = text.replace("\x00", "")
    return text.strip()


def truncate_for_embed_field(text: str, suffix: str = "...", max_length: int = EMBED_FIELD_VALUE_MAX) -> str:
    """Truncate text to fit in an embed field value.

    Args:
        text: Text to truncate.
        suffix: String appended when truncation occurs.
        max_length: Maximum allowed length (default: 1024).
    """
    if not text:
        return ""
    if len(suffix) >= max_length:
        return text[:max_length]
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def truncate(text: str, max_length: int, suffix: str = "...") -> str:
    """Truncate text to a maximum length, appending suffix if truncated."""
    if not text or len(text) <= max_length:
        return text or ""
    if len(suffix) >= max_length:
        return text[:max_length]
    return text[:max_length - len(suffix)] + suffix


def format_discord_timestamp(dt: datetime, style: str = "f") -> str:
    """Format a datetime as a Discord timestamp string.

    Styles: t (short time), T (long time), d (short date), D (long date),
    f (short datetime), F (long datetime), R (relative).
    """
    epoch = int(dt.timestamp())
    return f"<t:{epoch}:{style}>"


async def safe_send(
    messageable: discord.abc.Messageable, content: str = "", **kwargs
) -> "discord.Message | None":
    """Send a message, returning None on HTTPException instead of raising."""
    import discord as _discord

    try:
        return await messageable.send(content, **kwargs)
    except _discord.HTTPException as e:
        logger.warning(f"Failed to send message: {e}")
        return None


def paginate(text: str, page_size: int = 4096) -> list[str]:
    """Split text into chunks at line boundaries.

    Each chunk is at most ``page_size`` characters. Splits prefer line
    breaks so that output remains readable.
    """
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    if not text:
        return []
    if len(text) <= page_size:
        return [text]

    pages: list[str] = []
    while text:
        if len(text) <= page_size:
            pages.append(text)
            break
        # Find the last newline within page_size
        cut = text.rfind("\n", 0, page_size)
        if cut == -1:
            # No newline found — hard cut
            cut = page_size
        else:
            cut += 1  # include the newline
        pages.append(text[:cut])
        text = text[cut:]
    return pages
