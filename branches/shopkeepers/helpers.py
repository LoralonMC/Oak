"""
Shopkeepers Helper Functions
Shared utility functions for the shopkeepers system.
"""

import discord
import yaml
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def get_branch_dir():
    """Get the branch directory path."""
    return Path(__file__).parent


def get_db_path():
    """Get the database path for this branch."""
    return str(get_branch_dir() / "data.db")


def get_csv_directory(config: dict = None) -> str:
    """
    Get the CSV directory path. Falls back to the local csv_data/ folder
    inside the branch directory if not configured.
    """
    if config is None:
        config = get_shopkeepers_config()
    csv_dir = config.get("settings", {}).get("csv_directory", "")
    if not csv_dir:
        csv_dir = str(get_branch_dir() / "csv_data")
    return csv_dir


def get_shopkeepers_config():
    """Load shopkeepers config from config.yml."""
    config_path = Path(__file__).parent / "config.yml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        return config
    except Exception as e:
        logger.error(f"Failed to load shopkeepers config: {e}")
        return {}


def get_embed_color():
    """Get embed color from config."""
    config = get_shopkeepers_config()
    return config.get("settings", {}).get("ui", {}).get("embed_color", 0x50C878)


def get_admin_role_ids():
    """Get admin role IDs from config."""
    config = get_shopkeepers_config()
    return config.get("settings", {}).get("admin_role_ids", [])


def is_admin(interaction: discord.Interaction, admin_role_ids: list = None) -> bool:
    """
    Check if user has admin permissions.

    Args:
        interaction: Discord interaction
        admin_role_ids: Optional list of admin role IDs (will load from config if None)

    Returns:
        True if user is admin, False otherwise
    """
    if admin_role_ids is None:
        admin_role_ids = get_admin_role_ids()

    # Administrators always have access
    if interaction.user.guild_permissions.administrator:
        return True

    # Check if user has any admin roles
    return any(role.id in admin_role_ids for role in interaction.user.roles)


def format_item_name(display_name: str | None, search_name: str) -> str:
    """
    Format an item name for display.

    Uses display_name if available (custom items), otherwise title-cases
    the search_name (vanilla items).

    Args:
        display_name: Custom display name from NBT, or None
        search_name: Lowercase search name

    Returns:
        Properly capitalized display name
    """
    if display_name:
        return display_name
    return search_name.title()


def format_price(emeralds: float | None) -> str:
    """
    Convert a raw emerald count to a smart mixed-unit display string.

    Units: CEMB = 576 EM, EMB = 9 EM, EM = 1.
    Examples:
        None / 0   -> "N/A"
        0.005      -> "< 0.01 EM"
        2.5        -> "2.5 EM"
        17.4       -> "1 EMB 8.4 EM"
        576        -> "1 CEMB"
        594        -> "1 CEMB 2 EMB"
        600        -> "1 CEMB 2 EMB 6 EM"
    """
    if emeralds is None or emeralds <= 0:
        return "N/A"

    if emeralds < 0.01:
        return "< 0.01 EM"

    parts = []

    cemb = int(emeralds // 576)
    remainder = emeralds - cemb * 576
    if cemb > 0:
        parts.append(f"{cemb:,} CEMB")

    emb = int(remainder // 9)
    remainder = remainder - emb * 9
    if emb > 0:
        parts.append(f"{emb:,} EMB")

    if remainder > 0.001:
        formatted = f"{remainder:.2f}".rstrip("0").rstrip(".")
        parts.append(f"{formatted} EM")

    return " ".join(parts) if parts else "N/A"


def validate_config(config: dict) -> tuple:
    """
    Validate shopkeepers configuration.

    Args:
        config: Configuration dictionary

    Returns:
        Tuple of (is_valid: bool, errors: list)
    """
    errors = []

    settings = config.get("settings", {})

    # Validate csv_directory (falls back to local csv_data/ folder)
    csv_directory = get_csv_directory(config)
    csv_path = Path(csv_directory)
    if not csv_path.exists():
        errors.append(f"csv_directory does not exist: {csv_directory}")

    # Validate currencies
    currencies = settings.get("currencies", [])
    if not isinstance(currencies, list):
        errors.append("currencies must be a list")
    elif not currencies:
        errors.append("No currencies configured")
    else:
        for i, currency in enumerate(currencies):
            if not isinstance(currency, dict):
                errors.append(f"Currency entry {i} must be a dict")
                continue
            if "item_key" not in currency:
                errors.append(f"Currency entry {i} missing item_key")
            if "emerald_value" not in currency:
                errors.append(f"Currency entry {i} missing emerald_value")

    return (len(errors) == 0, errors)
