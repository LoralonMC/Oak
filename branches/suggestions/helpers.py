"""Suggestions helper functions."""

import logging

from oak.constants import EMBED_FIELD_VALUE_MAX
from oak.utils import truncate_for_embed_field

logger = logging.getLogger(__name__)


def get_embed_colors(config: dict) -> dict:
    """Get embed colors from config.

    Args:
        config: Suggestions configuration dictionary

    Returns:
        Dict mapping status names to integer color values
    """
    ui_settings = config.get("settings", {}).get("ui", {})
    embed_colors = ui_settings.get("embed_colors", {})
    return {
        "pending": embed_colors.get("pending", 0x2B2D31),
        "approved": embed_colors.get("approved", 0x57F287),
        "denied": embed_colors.get("denied", 0xED4245),
    }


def truncate(text: str, limit: int = EMBED_FIELD_VALUE_MAX) -> str:
    """Truncate text for embed fields."""
    return truncate_for_embed_field(text, "…", max_length=limit)
