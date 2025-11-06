"""
Suggestions Branch Implementation
Handles user suggestions with voting and management
"""

import discord
from discord.ext import commands
from discord import ui, Interaction
from database import init_cog_database
from utils import sanitize_text, truncate_text
import aiosqlite
import json
import time
import logging
from pathlib import Path
import yaml

logger = logging.getLogger(__name__)

# Database path - set by Suggestions branch when loaded
_DB_PATH = None

def get_db_path():
    """Get the database path for this branch."""
    if _DB_PATH is None:
        # Fallback to branch folder
        return str(Path(__file__).parent / "data.db")
    return _DB_PATH

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

MAX_FIELD_LENGTH = 1024

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


def truncate(text: str, limit: int = MAX_FIELD_LENGTH) -> str:
    """Truncate text for embed fields."""
    return truncate_text(text, limit, '…')


class DummyView(ui.View):
    def __init__(self, status="Pending"):
        super().__init__(timeout=None)
        self.status = status

    @discord.ui.button(label="👍 Like", style=discord.ButtonStyle.green, custom_id="suggestion_like")
    async def like(self, interaction: Interaction, button: discord.ui.Button):
        await handle_vote_button(interaction, "like")

    @discord.ui.button(label="👎 Dislike", style=discord.ButtonStyle.red, custom_id="suggestion_dislike")
    async def dislike(self, interaction: Interaction, button: discord.ui.Button):
        await handle_vote_button(interaction, "dislike")

    @discord.ui.button(label="⚙️ Manage", style=discord.ButtonStyle.gray, custom_id="suggestion_manage")
    async def manage(self, interaction: Interaction, button: discord.ui.Button):
        await handle_manage_button(interaction)


async def handle_vote_button(interaction: Interaction, vote_type: str):
    message_id = interaction.message.id

    try:
        async with aiosqlite.connect(get_db_path()) as db:
            cursor = await db.execute("SELECT likes, dislikes, status FROM suggestions WHERE message_id = ?", (message_id,))
            row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message("Suggestion not found.", ephemeral=True)
                return

            likes, dislikes, status = json.loads(row[0]), json.loads(row[1]), row[2]

            if vote_type == "like":
                if interaction.user.id in likes:
                    likes.remove(interaction.user.id)
                else:
                    likes.append(interaction.user.id)
                    if interaction.user.id in dislikes:
                        dislikes.remove(interaction.user.id)
            else:
                if interaction.user.id in dislikes:
                    dislikes.remove(interaction.user.id)
                else:
                    dislikes.append(interaction.user.id)
                    if interaction.user.id in likes:
                        likes.remove(interaction.user.id)

            await db.execute("UPDATE suggestions SET likes = ?, dislikes = ? WHERE message_id = ?",
                             (json.dumps(likes), json.dumps(dislikes), message_id))
            await db.commit()

        embed = interaction.message.embeds[0]
        embed.set_field_at(1, name="📊 Statistics", value=f"**{len(likes)}** Likes\n**{len(dislikes)}** Dislikes\nStatus: **{status}**", inline=True)

        view = DummyView(status=status)

        await interaction.message.edit(embed=embed, view=view)
        await interaction.response.defer()

    except discord.HTTPException as e:
        logger.error(f"Failed to edit message or respond: {e}")
        try:
            await interaction.response.send_message("Failed to update vote.", ephemeral=True)
        except:
            pass
    except Exception as e:
        logger.error(f"Error handling vote button: {e}")
        try:
            await interaction.response.send_message("An error occurred.", ephemeral=True)
        except:
            pass


async def handle_manage_button(interaction: Interaction):
    # Load config to get manager role IDs
    config_path = Path(__file__).parent / "config.yml"
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
        manager_role_ids = config.get("settings", {}).get("manager_role_ids", [])
    except Exception as e:
        logger.error(f"Failed to load suggestions config: {e}")
        manager_role_ids = []

    message_id = interaction.message.id
    user_role_ids = [role.id for role in interaction.user.roles]
    if not any(role_id in manager_role_ids for role_id in user_role_ids):
        await interaction.response.send_message("You don't have permission to manage suggestions.", ephemeral=True)
        return

    async with aiosqlite.connect(get_db_path()) as db:
        cursor = await db.execute("SELECT user_id FROM suggestions WHERE message_id = ?", (message_id,))
        row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message("This suggestion could not be found in the database.", ephemeral=True)
            return

    view = ManageSuggestionView(message_id)
    await interaction.response.send_message("Manage this suggestion:", view=view, ephemeral=True)


class ManageSuggestionView(ui.View):
    def __init__(self, message_id):
        super().__init__(timeout=60)
        self.message_id = message_id

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(StatusModal(self.message_id, "Approved"))

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.red)
    async def deny(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(StatusModal(self.message_id, "Denied"))

    @discord.ui.button(label="🗑️ Delete", style=discord.ButtonStyle.gray)
    async def delete(self, interaction: Interaction, button: discord.ui.Button):
        thread_deleted = False

        async with aiosqlite.connect(get_db_path()) as db:
            cursor = await db.execute("SELECT thread_id FROM suggestions WHERE message_id = ?", (self.message_id,))
            row = await cursor.fetchone()

            if row:
                thread_id = row[0]
                thread_channel = interaction.guild.get_thread(thread_id)
                if thread_channel:
                    try:
                        await thread_channel.delete()
                        thread_deleted = True
                    except discord.Forbidden:
                        logger.warning(f"Missing permissions to delete thread {thread_id}")
                    except discord.HTTPException:
                        logger.warning(f"Failed to delete thread {thread_id}")

            await db.execute("DELETE FROM suggestions WHERE message_id = ?", (self.message_id,))
            await db.commit()

        try:
            message = await interaction.channel.fetch_message(self.message_id)
            await message.delete()
        except discord.NotFound:
            pass

        await interaction.response.send_message(
            f"Suggestion deleted{' along with its thread.' if thread_deleted else '.'}",
            ephemeral=True
        )


class StatusModal(ui.Modal, title="Reason for Action"):
    def __init__(self, message_id, status):
        super().__init__()
        self.message_id = message_id
        self.status = status

        self.reason = ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, required=True)
        self.add_item(self.reason)

    async def on_submit(self, interaction: Interaction):
        async with aiosqlite.connect(get_db_path()) as db:
            await db.execute("UPDATE suggestions SET status = ?, reason = ? WHERE message_id = ?",
                             (self.status, self.reason.value, self.message_id))
            await db.commit()

            cursor = await db.execute("SELECT user_id, content, likes, dislikes FROM suggestions WHERE message_id = ?", (self.message_id,))
            row = await cursor.fetchone()
            if not row:
                await interaction.response.send_message("This suggestion could not be found in the database.", ephemeral=True)
                return

            user_id, content, likes, dislikes = row
            likes = json.loads(likes)
            dislikes = json.loads(dislikes)

        try:
            message = await interaction.channel.fetch_message(self.message_id)
        except discord.NotFound:
            await interaction.response.send_message("Suggestion message not found.", ephemeral=True)
            return

        embed = message.embeds[0]
        embed.title = f"💡 {self.status} Suggestion"
        author = interaction.guild.get_member(user_id)
        if author is not None and author.display_avatar:
            embed.set_thumbnail(url=author.display_avatar.url)
        embed.color = discord.Color.green() if self.status == "Approved" else discord.Color.red()
        embed.clear_fields()
        embed.add_field(name="💬 Suggestion", value=truncate(content), inline=False)
        embed.add_field(name="📊 Statistics", value=f"**{len(likes)}** Likes\n**{len(dislikes)}** Dislikes\nStatus: **{self.status}**", inline=True)

        if author:
            author_text = f"{author.mention} ({author.name})"
        else:
            author_text = f"<@{user_id}>"
        embed.add_field(name="👤 Author", value=author_text, inline=True)

        moderator = interaction.user
        unix_timestamp = int(time.time())
        discord_timestamp = f"<t:{unix_timestamp}:f>"
        reason_text = f"{self.reason.value}\n\n— {moderator.mention} ({discord_timestamp})"
        embed.add_field(name=f"📝 Reason for {self.status}", value=reason_text, inline=False)

        view = DummyView(status=self.status)

        await message.edit(embed=embed, view=view)
        await interaction.response.send_message(f"Suggestion {self.status.lower()}!", ephemeral=True)

        user = interaction.client.get_user(user_id)
        if user:
            try:
                suggestion_link = f"https://discord.com/channels/{interaction.guild.id}/{interaction.channel.id}/{self.message_id}"
                dm_content = (
                    f"Your suggestion was **{self.status.lower()}**!\n"
                    f"**Reason:** {self.reason.value}\n"
                    f"[Click here to view your suggestion]({suggestion_link})"
                )

                await user.send(dm_content)

            except discord.Forbidden:
                pass


class Suggestions(commands.Cog):
    """Handles user suggestions with voting and management."""

    def __init__(self, bot):
        global _DB_PATH
        self.bot = bot

        # Set database path (in this branch's folder)
        self.db_path = str(Path(__file__).parent / "data.db")
        _DB_PATH = self.db_path  # Set module-level path for standalone functions

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

        logger.info(f"Suggestions branch initialized (channel: {self.channel_id}, db: {self.db_path})")

    async def cog_load(self):
        """Initialize database when branch is loaded."""
        await init_cog_database(self.db_path, SUGGESTIONS_SCHEMA, "Suggestions")

    def load_config(self) -> dict:
        """Load config from config.yml in this branch's folder."""
        config_path = Path(__file__).parent / "config.yml"

        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    config = yaml.safe_load(f) or {}
                logger.info(f"Loaded config for Suggestions")
                return config
            except Exception as e:
                logger.error(f"Failed to load config for Suggestions: {e}")

        # Return default config if file doesn't exist
        return DEFAULT_CONFIG

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
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
                color=discord.Color.dark_embed()
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
