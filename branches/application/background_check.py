"""
Background Check Integration
MySQL/Plan integration for fetching player statistics and punishment history.
"""

import discord
import mysql.connector
import logging
from .helpers import get_application_config, get_embed_colors

logger = logging.getLogger(__name__)


async def fetch_playtime_embed(mc_name: str) -> discord.Embed:
    """
    Fetch playtime data from Plan DB/MySQL.

    Args:
        mc_name: Minecraft username

    Returns:
        Discord embed with playtime information
    """
    config = get_application_config()
    mysql_config = config.get("settings", {}).get("mysql", {})

    if not mysql_config.get("enabled", False):
        return discord.Embed(
            title="Playtime Data",
            description="MySQL/Plan is not enabled in config.",
            color=get_embed_colors()["error"]
        )

    conn = None
    cursor = None

    try:
        # Build MySQL connection config
        mysql_conn_config = {
            "host": mysql_config.get("host"),
            "user": mysql_config.get("user"),
            "password": mysql_config.get("password"),
            "database": mysql_config.get("database"),
        }
        conn = mysql.connector.connect(**mysql_conn_config)
        cursor = conn.cursor(dictionary=True)

        # Get player UUID
        cursor.execute("SELECT uuid FROM plan_users WHERE name = %s", (mc_name,))
        row = cursor.fetchone()

        if not row:
            return discord.Embed(
                title="Playtime Data",
                description=f"No player found with username **{mc_name}**.",
                color=get_embed_colors()["warning"]
            )

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

        if stats:
            def fmt(seconds):
                """Format seconds to hours and minutes."""
                hours = seconds // 3600
                minutes = (seconds % 3600) // 60
                return f"{int(hours)}h {int(minutes)}m"

            embed = discord.Embed(
                title="Playtime Data",
                description=f"Playtime for **{mc_name}**",
                color=get_embed_colors()["info"]
            )
            embed.add_field(name="Last 30 days", value=fmt(stats['last_30_days']))
            embed.add_field(name="Last 7 days", value=fmt(stats['last_7_days']))
        else:
            embed = discord.Embed(
                title="Playtime Data",
                description=f"No playtime stats found for **{mc_name}**.",
                color=get_embed_colors()["warning"]
            )

    except mysql.connector.Error as e:
        logger.error(f"MySQL error fetching playtime for {mc_name}: {e}")
        embed = discord.Embed(
            title="Playtime Data",
            description=f"Database error: {str(e)}",
            color=get_embed_colors()["error"]
        )

    except Exception as e:
        logger.error(f"Unexpected error fetching playtime for {mc_name}: {e}")
        embed = discord.Embed(
            title="Playtime Data",
            description=f"Error: {str(e)}",
            color=get_embed_colors()["error"]
        )

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

    return embed
