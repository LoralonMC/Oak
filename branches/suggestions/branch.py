"""
Suggestions Branch Implementation
Handles user suggestions with voting and management
"""

import discord
from discord.ext import commands
from database import init_branch_database
from utils import sanitize_text
import aiosqlite
import json
import logging
from pathlib import Path

# Import modularized components
from .helpers import (
    get_db_path,
    get_suggestions_config,
    get_embed_colors,
    truncate
)
from .views import DummyView

logger = logging.getLogger(__name__)


# Database schema for suggestions
SUGGESTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER UNIQUE,
    thread_id INTEGER,
    user_id INTEGER,
    content TEXT,
    likes TEXT,
    dislikes TEXT,
    status TEXT,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# Default configuration for this branch
DEFAULT_CONFIG = {
    "enabled": True,
    "version": "1.0.0",
    "settings": {
        "channel_id": 0,  # Replace with your actual channel ID
        "manager_role_ids": [],  # Replace with your actual role IDs

        "validation": {
            "min_length": 10,
            "max_length": 4000,
        },

        "ui": {
            "embed_colors": {
                "pending": 0x2B2D31,
                "approved": 0x57F287,
                "denied": 0xED4245,
            },
            "thread": {
                "title_max_length": 40,
                "title_prefix": "💬 Discussion: ",
            }
        },

        "messages": {
            "too_short": "Your suggestion is too short. Please provide more detail (at least 10 characters).",
            "empty": "Your suggestion was empty or invalid.",
            "created_error": "Failed to create your suggestion. Please try again later.",
            "not_found": "Suggestion not found.",
            "no_permission": "You don't have permission to manage suggestions.",
            "vote_failed": "Failed to update vote.",
        }
    }
}


class Suggestions(commands.Cog):
    """Handles user suggestions with voting and management."""

    def __init__(self, bot):
        self.bot = bot

        # Set database path (in this branch's folder)
        self.db_path = get_db_path()

        # Load config
        self.config = self.load_config()

        # Access settings
        self.channel_id = self.config.get("settings", {}).get("channel_id")
        self.manager_role_ids = self.config.get("settings", {}).get("manager_role_ids", [])

        validation = self.config.get("settings", {}).get("validation", {})
        self.min_length = validation.get("min_length", 10)
        self.max_length = validation.get("max_length", 4000)

        messages = self.config.get("settings", {}).get("messages", {})
        self.msg_too_short = messages.get("too_short", DEFAULT_CONFIG["settings"]["messages"]["too_short"])
        self.msg_empty = messages.get("empty", DEFAULT_CONFIG["settings"]["messages"]["empty"])
        self.msg_error = messages.get("created_error", DEFAULT_CONFIG["settings"]["messages"]["created_error"])

        # Load UI settings
        colors = get_embed_colors()
        self.color_pending = colors["pending"]
        self.color_approved = colors["approved"]
        self.color_denied = colors["denied"]

        logger.info(f"Suggestions branch initialized (channel: {self.channel_id}, db: {self.db_path})")

    async def cog_load(self):
        """Initialize database when branch is loaded."""
        await init_branch_database(self.db_path, SUGGESTIONS_SCHEMA, "Suggestions")

        # Register persistent views
        logger.info("Registering DummyView for persistent interactions")
        self.bot.add_view(DummyView())

    def load_config(self) -> dict:
        """Load config from config.yml in this branch's folder."""
        from utils import load_branch_config
        config_path = Path(__file__).parent / "config.yml"
        return load_branch_config(config_path, DEFAULT_CONFIG, "Suggestions")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle new messages in the suggestions channel."""
        if message.author.bot or message.channel.id != self.channel_id:
            return

        # Validate and sanitize content
        content = sanitize_text(message.content, max_length=self.max_length)
        if not content:
            try:
                await message.author.send(self.msg_empty)
                await message.delete()
            except discord.Forbidden:
                pass
            return

        if len(content) < self.min_length:
            try:
                await message.author.send(self.msg_too_short)
                await message.delete()
            except discord.Forbidden:
                pass
            return

        try:
            embed = discord.Embed(
                title="💡 New Pending Suggestion",
                color=self.color_pending
            )
            embed.set_thumbnail(url=message.author.display_avatar.url)
            embed.add_field(name="💬 Suggestion", value=truncate(content), inline=False)
            embed.add_field(name="📊 Statistics", value=f"**0** Likes\n**0** Dislikes\nStatus: **Pending**", inline=True)
            embed.add_field(name="👤 Author", value=f"{message.author.mention} ({message.author.name})", inline=True)

            view = DummyView()
            sent = await message.channel.send(embed=embed, view=view)

            # Create thread with sanitized title
            thread_settings = self.config.get("settings", {}).get("ui", {}).get("thread", {})
            title_max = thread_settings.get("title_max_length", 40)
            title_prefix = thread_settings.get("title_prefix", "💬 Discussion: ")

            raw_title = sanitize_text(content.strip()[:title_max], max_length=title_max)
            thread_title = f"{title_prefix}{raw_title}" if raw_title else f"{title_prefix}{message.author.display_name}"
            thread = await sent.create_thread(name=thread_title)

            # Save to database
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                INSERT INTO suggestions (message_id, thread_id, user_id, content, likes, dislikes, status, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sent.id, thread.id, message.author.id, content,
                    json.dumps([]), json.dumps([]), "Pending", None
                ))
                await db.commit()

            await message.delete()
            logger.info(f"New suggestion from {message.author} (ID: {message.author.id})")

        except discord.HTTPException as e:
            logger.error(f"Failed to create suggestion: {e}")
            try:
                await message.author.send(self.msg_error)
            except discord.Forbidden:
                pass
        except Exception as e:
            logger.error(f"Unexpected error creating suggestion: {e}")
            try:
                await message.author.send("An error occurred while creating your suggestion.")
            except discord.Forbidden:
                pass

    def cog_unload(self):
        """Called when the branch is unloaded."""
        logger.info(f"Suggestions branch unloaded")
