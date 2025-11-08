"""
Suggestions Helper Functions
Shared utility functions for the suggestions system.
"""

import yaml
import logging
from pathlib import Path
from typing import Dict, Any
from constants import EMBED_FIELD_VALUE_MAX, truncate_for_embed_field

logger = logging.getLogger(__name__)


def get_db_path():
    """Get the database path for this branch."""
    return str(Path(__file__).parent / "data.db")


def get_suggestions_config():
    """Load suggestions config from config.yml."""
    config_path = Path(__file__).parent / "config.yml"
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
        logger.debug("Loaded suggestions config")
        return config
    except Exception as e:
        logger.error(f"Failed to load suggestions config: {e}")
        return {}


def get_embed_colors():
    """Get embed colors from config."""
    config = get_suggestions_config()
    ui_settings = config.get("settings", {}).get("ui", {})
    embed_colors = ui_settings.get("embed_colors", {})
    return {
        "pending": embed_colors.get("pending", 0x2B2D31),
        "approved": embed_colors.get("approved", 0x57F287),
        "denied": embed_colors.get("denied", 0xED4245),
    }


def get_manager_role_ids():
    """Get manager role IDs from config."""
    config = get_suggestions_config()
    return config.get("settings", {}).get("manager_role_ids", [])


def truncate(text: str, limit: int = EMBED_FIELD_VALUE_MAX) -> str:
    """Truncate text for embed fields."""
    return truncate_for_embed_field(text, '…')
