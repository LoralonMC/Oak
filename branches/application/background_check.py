"""
Background Check Integration
MySQL/Plan integration for fetching player statistics and punishment history.
"""

import asyncio
import discord
import mysql.connector
import logging
from .helpers import get_embed_colors

logger = logging.getLogger(__name__)


def _sync_fetch_playtime(db_config: dict, mc_name: str) -> dict:
    """
    Synchronous function that performs blocking MySQL operations.
    Called via asyncio.to_thread() to avoid blocking the event loop.

    Args:
        db_config: MySQL connection configuration dict
        mc_name: Minecraft username

    Returns:
        dict with keys: 'found' (bool), 'stats' (dict or None), 'error' (str or None)
    """
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)

        # Get player UUID
        cursor.execute("SELECT uuid FROM plan_users WHERE name = %s", (mc_name,))
        row = cursor.fetchone()

        if not row:
            return {"found": False, "stats": None, "error": None}

        uuid = row['uuid']

        # Get playtime statistics
        query = """
        SELECT
            COALESCE(SUM(CASE
                WHEN s.session_start > UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 30 DAY)) * 1000
                THEN (s.session_end - s.session_start - IFNULL(s.afk_time, 0)) / 1000
                ELSE 0
            END), 0) as last_30_days,
            COALESCE(SUM(CASE
                WHEN s.session_start > UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 7 DAY)) * 1000
                THEN (s.session_end - s.session_start - IFNULL(s.afk_time, 0)) / 1000
                ELSE 0
            END), 0) as last_7_days
        FROM plan_users u
        LEFT JOIN plan_sessions s ON s.user_id = u.id
        WHERE u.uuid = %s
        GROUP BY u.id, u.name, u.uuid
        """
        cursor.execute(query, (uuid,))
        stats = cursor.fetchone()

        return {"found": True, "stats": stats, "error": None}

    except mysql.connector.Error as e:
        logger.error(f"MySQL error fetching playtime for {mc_name}: {e}")
        return {"found": False, "stats": None, "error": "database"}

    except Exception as e:
        logger.error(f"Unexpected error fetching playtime for {mc_name}: {e}")
        return {"found": False, "stats": None, "error": "unexpected"}

    finally:
        if cursor:
            try:
                cursor.close()
            except Exception as e:
                logger.error(f"Error closing cursor: {e}")
        if conn:
            try:
                conn.close()
            except Exception as e:
                logger.error(f"Error closing connection: {e}")


async def fetch_playtime_embed(mc_name: str, config: dict) -> discord.Embed:
    """
    Fetch playtime data from Plan DB/MySQL.

    Args:
        mc_name: Minecraft username
        config: Application configuration dictionary

    Returns:
        Discord embed with playtime information
    """
    colors = get_embed_colors(config)
    mysql_config = config.get("settings", {}).get("mysql", {})

    if not mysql_config.get("enabled", False):
        return discord.Embed(
            title="Playtime Data",
            description="MySQL/Plan is not enabled in config.",
            color=colors["error"]
        )

    # Build MySQL connection config
    db_config = {
        "host": mysql_config.get("host"),
        "user": mysql_config.get("user"),
        "password": mysql_config.get("password"),
        "database": mysql_config.get("database"),
        "connect_timeout": 10,
    }

    # Run blocking MySQL operations in a thread to avoid blocking the event loop
    result = await asyncio.to_thread(_sync_fetch_playtime, db_config, mc_name)

    if result["error"] == "database":
        return discord.Embed(
            title="Playtime Data",
            description="A database error occurred while fetching playtime data.",
            color=colors["error"]
        )

    if result["error"] == "unexpected":
        return discord.Embed(
            title="Playtime Data",
            description="An unexpected error occurred while fetching playtime data.",
            color=colors["error"]
        )

    if not result["found"]:
        return discord.Embed(
            title="Playtime Data",
            description=f"No player found with username **{mc_name}**.",
            color=colors["warning"]
        )

    stats = result["stats"]
    if stats:
        def fmt(seconds):
            """Format seconds to hours and minutes."""
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{int(hours)}h {int(minutes)}m"

        embed = discord.Embed(
            title="Playtime Data",
            description=f"Playtime for **{mc_name}**",
            color=colors["info"]
        )
        embed.add_field(name="Last 30 days", value=fmt(stats['last_30_days']))
        embed.add_field(name="Last 7 days", value=fmt(stats['last_7_days']))
    else:
        embed = discord.Embed(
            title="Playtime Data",
            description=f"No playtime stats found for **{mc_name}**.",
            color=colors["warning"]
        )

    return embed
