"""
Ticket System Helper Functions
Shared utility functions for the ticket system.
"""

import discord
import yaml
import hashlib
import json
import logging
import re
import aiosqlite
import asyncio
from pathlib import Path

logger = logging.getLogger(__name__)


def get_db_path():
    """Get the database path for this branch."""
    return str(Path(__file__).parent / "data.db")


def get_tickets_config():
    """Load tickets config from config.yml."""
    config_path = Path(__file__).parent / "config.yml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        return config
    except Exception as e:
        logger.error(f"Failed to load tickets config: {e}")
        return {}


def get_embed_colors():
    """Get embed colors from config."""
    config = get_tickets_config()
    colors = config.get("settings", {}).get("ui", {}).get("colors", {})
    return {
        "open": colors.get("open", 0x5865F2),
        "closed": colors.get("closed", 0x99AAB5),
        "log_created": colors.get("log_created", 0x57F287),
        "log_closed": colors.get("log_closed", 0xED4245),
        "log_reopened": colors.get("log_reopened", 0xFEE75C)
    }


def get_staff_role_ids():
    """Get staff role IDs from config."""
    config = get_tickets_config()
    return config.get("settings", {}).get("staff_role_ids", [])


def is_staff(interaction: discord.Interaction, staff_role_ids: list = None) -> bool:
    """
    Check if user has staff permissions.

    Args:
        interaction: Discord interaction
        staff_role_ids: Optional list of staff role IDs (will load from config if None)

    Returns:
        True if user is staff, False otherwise
    """
    if staff_role_ids is None:
        staff_role_ids = get_staff_role_ids()

    # Administrators always have access
    if interaction.user.guild_permissions.administrator:
        return True

    # Check if user has any staff roles
    return any(role.id in staff_role_ids for role in interaction.user.roles)


def can_manage_ticket_category(interaction: discord.Interaction, category: str) -> bool:
    """
    Check if user can manage tickets in a specific category.

    Users can manage a category if they have:
    - Global staff role (from staff_role_ids), OR
    - Category-specific role (from that category's staff_roles)

    Args:
        interaction: Discord interaction
        category: Ticket category key

    Returns:
        True if user can manage this category, False otherwise
    """
    # Administrators always have access
    if interaction.user.guild_permissions.administrator:
        return True

    config = get_tickets_config()

    # Check global staff roles
    global_staff_role_ids = config.get("settings", {}).get("staff_role_ids", [])
    if any(role.id in global_staff_role_ids for role in interaction.user.roles):
        return True

    # Check category-specific staff_roles (with backwards compatibility for ping_roles)
    categories = config.get("settings", {}).get("categories", {})
    category_config = categories.get(category, {})
    staff_roles = category_config.get("staff_roles", category_config.get("ping_roles", []))

    if any(role.id in staff_roles for role in interaction.user.roles):
        return True

    return False


def can_bypass_duplicate_check(interaction: discord.Interaction) -> bool:
    """
    Check if user can bypass the 1 ticket per category restriction.

    Useful for staff who need to create tickets on behalf of users.

    Args:
        interaction: Discord interaction

    Returns:
        True if user can bypass duplicate check, False otherwise
    """
    config = get_tickets_config()
    bypass_role_ids = config.get("settings", {}).get("bypass_duplicate_check_role_ids", [])

    # Administrators can always bypass
    if interaction.user.guild_permissions.administrator:
        return True

    # Check if user has any bypass roles
    return any(role.id in bypass_role_ids for role in interaction.user.roles)


def sanitize_name(name: str, user_id: int = None) -> str:
    """
    Sanitize username for use in thread names.

    Args:
        name: The name to sanitize
        user_id: User ID for fallback (optional)

    Returns:
        Sanitized name safe for Discord thread names
    """
    # Remove special characters, keep alphanumeric, -, _
    sanitized = re.sub(r'[^a-zA-Z0-9\-_]', '', name)

    # Limit length to 100 characters (Discord's thread name limit)
    sanitized = sanitized[:100]

    # Fallback if empty
    if not sanitized:
        if user_id:
            sanitized = f"user-{user_id}"
        else:
            sanitized = "ticket"

    return sanitized.lower()


async def get_next_ticket_number(category: str, db: aiosqlite.Connection, max_retries: int = 3) -> int:
    """
    Generate next ticket number with race condition protection.

    Uses IMMEDIATE transaction to prevent concurrent writes and retries on collision.

    Args:
        category: Ticket category key
        db: Database connection
        max_retries: Maximum number of retry attempts

    Returns:
        Next ticket number for this category
    """
    for attempt in range(max_retries):
        try:
            # Use IMMEDIATE transaction to lock database
            await db.execute("BEGIN IMMEDIATE")

            # Get max number for this category
            cursor = await db.execute(
                "SELECT MAX(ticket_number) FROM tickets WHERE category = ?",
                (category,)
            )
            row = await cursor.fetchone()
            max_num = row[0] if row and row[0] else 0
            next_num = max_num + 1

            # Commit transaction
            await db.commit()

            return next_num
        except aiosqlite.IntegrityError:
            # Collision detected, rollback and retry
            await db.rollback()
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(0.1 * (attempt + 1))  # Exponential backoff
        except Exception:
            await db.rollback()
            raise


async def has_active_ticket(user_id: int, category: str, db_path: str) -> tuple:
    """
    Check if user has an active ticket in the given category.

    Args:
        user_id: Discord user ID
        category: Ticket category key
        db_path: Path to database

    Returns:
        Tuple of (has_ticket: bool, thread_id: int or None)
    """
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT thread_id FROM tickets WHERE user_id = ? AND category = ? AND status = 'open'",
            (user_id, category)
        )
        row = await cursor.fetchone()

        if row:
            return True, row[0]
        return False, None


def hash_config(config: dict) -> str:
    """
    Generate SHA-256 hash of config for change detection.

    Args:
        config: Configuration dictionary

    Returns:
        SHA-256 hash string
    """
    # Normalize and hash config
    normalized = json.dumps(config, sort_keys=True)
    return hashlib.sha256(normalized.encode()).hexdigest()


def validate_config(config: dict) -> tuple:
    """
    Validate ticket configuration.

    Args:
        config: Configuration dictionary

    Returns:
        Tuple of (is_valid: bool, errors: list)
    """
    errors = []

    settings = config.get('settings', {})

    # Validate channel IDs
    panel_channel = settings.get('ticket_panel_channel_id', 0)
    if panel_channel == 0:
        errors.append("ticket_panel_channel_id not configured")

    log_channel = settings.get('log_channel_id', 0)
    if log_channel != 0 and (log_channel < 0 or log_channel > 2**63):
        errors.append("Invalid log_channel_id")

    # Validate staff roles
    staff_roles = settings.get('staff_role_ids', [])
    if not isinstance(staff_roles, list):
        errors.append("staff_role_ids must be a list")
    elif not staff_roles:
        errors.append("No staff roles configured")

    # Validate categories
    categories = settings.get('categories', {})
    if not categories:
        errors.append("No categories configured")

    for cat_key, cat_config in categories.items():
        if not cat_config.get('enabled', True):
            continue

        # Check required fields
        if 'naming_pattern' not in cat_config:
            errors.append(f"Category '{cat_key}' missing naming_pattern")

        if 'welcome_message' not in cat_config:
            errors.append(f"Category '{cat_key}' missing welcome_message")

        # Validate naming pattern
        pattern = cat_config.get('naming_pattern', '')
        valid_vars = ['{number}', '{nickname}', '{username}']
        if not any(var in pattern for var in valid_vars):
            errors.append(f"Category '{cat_key}' has invalid naming_pattern (must contain {{number}}, {{nickname}}, or {{username}})")

    return (len(errors) == 0, errors)


def format_log_embed(event_type: str, ticket_data: dict, user: discord.User = None, reason: str = None) -> discord.Embed:
    """
    Create a formatted embed for logging ticket events.

    Args:
        event_type: Type of event ('created', 'closed', 'reopened')
        ticket_data: Dictionary with ticket information
        user: User who performed the action (optional)
        reason: Reason for closing (optional)

    Returns:
        Discord embed for logging
    """
    colors = get_embed_colors()

    if event_type == 'created':
        embed = discord.Embed(
            title="🎫 Ticket Created",
            color=colors["log_created"],
            timestamp=discord.utils.utcnow()
        )
    elif event_type == 'closed':
        embed = discord.Embed(
            title="🔒 Ticket Closed",
            color=colors["log_closed"],
            timestamp=discord.utils.utcnow()
        )
    elif event_type == 'reopened':
        embed = discord.Embed(
            title="🔓 Ticket Reopened",
            color=colors["log_reopened"],
            timestamp=discord.utils.utcnow()
        )
    else:
        embed = discord.Embed(
            title="📋 Ticket Event",
            color=colors["open"],
            timestamp=discord.utils.utcnow()
        )

    # Add ticket information
    embed.add_field(
        name="Category",
        value=ticket_data.get('category', 'Unknown').replace('_', ' ').title(),
        inline=True
    )

    if 'thread_id' in ticket_data:
        embed.add_field(
            name="Thread",
            value=f"<#{ticket_data['thread_id']}>",
            inline=True
        )

    if 'creator_id' in ticket_data:
        embed.add_field(
            name="Creator",
            value=f"<@{ticket_data['creator_id']}>",
            inline=True
        )

    if user:
        embed.add_field(
            name="Action By",
            value=user.mention,
            inline=True
        )

    if reason:
        embed.add_field(
            name="Reason",
            value=reason[:1000],  # Limit to 1000 chars
            inline=False
        )

    return embed


def check_permissions(channel: discord.TextChannel) -> list:
    """
    Check if bot has required permissions in the channel.

    Args:
        channel: Channel to check permissions in

    Returns:
        List of missing permission names (empty if all permissions present)
    """
    missing = []
    required = [
        'send_messages',
        'create_private_threads',
        'manage_threads',
        'send_messages_in_threads',
        'read_message_history',
        'manage_messages',
        'embed_links'
    ]

    perms = channel.permissions_for(channel.guild.me)
    for perm in required:
        if not getattr(perms, perm, False):
            missing.append(perm)

    return missing


def parse_time_string(time_str: str) -> int:
    """
    Parse a time string into seconds.

    Supports formats like:
    - "30m", "1h", "2h", "1d", "3d"
    - "30", "60", "120" (assumed to be minutes)

    Maximum allowed: 30 days (43200m, 720h, 30d)

    Args:
        time_str: Time string to parse

    Returns:
        Number of seconds, or None if invalid or exceeds maximum

    Examples:
        parse_time_string("30m") -> 1800
        parse_time_string("2h") -> 7200
        parse_time_string("1d") -> 86400
        parse_time_string("60") -> 3600
        parse_time_string("9999d") -> None (exceeds max)
    """
    if not time_str:
        return None

    time_str = time_str.strip().lower()

    # Match pattern like "30m", "2h", "1d"
    match = re.match(r'^(\d+)([mhd])$', time_str)
    if match:
        value = int(match.group(1))
        unit = match.group(2)

        # Enforce reasonable limits (max 30 days)
        if unit == 'm':  # minutes
            if value > 43200:  # 30 days in minutes
                return None
            return value * 60
        elif unit == 'h':  # hours
            if value > 720:  # 30 days in hours
                return None
            return value * 3600
        elif unit == 'd':  # days
            if value > 30:  # 30 days max
                return None
            return value * 86400

    # Try parsing as plain number (assume minutes)
    match = re.match(r'^(\d+)$', time_str)
    if match:
        value = int(match.group(1))
        if value > 43200:  # 30 days in minutes
            return None
        return value * 60  # Assume minutes

    return None
