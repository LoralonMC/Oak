"""Suggestions branch — user suggestions with voting and management."""

import json

import discord
from discord.ext import commands

from oak import OakBranch
from oak.context import BranchContext
from oak.utils import sanitize_text

from .helpers import get_db_path, truncate
from .views import SuggestionVoteView

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
);
CREATE TABLE IF NOT EXISTS suggestion_votes (
    suggestion_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    vote_type TEXT NOT NULL CHECK(vote_type IN ('like', 'dislike')),
    PRIMARY KEY (suggestion_id, user_id)
);
"""

DEFAULT_CONFIG = {
    "enabled": True,
    "version": "1.0.0",
    "settings": {
        "channel_id": 0,
        "manager_role_ids": [],
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
            },
        },
        "messages": {
            "too_short": "Your suggestion is too short. Please provide more detail (at least 10 characters).",
            "empty": "Your suggestion was empty or invalid.",
            "created_error": "Failed to create your suggestion. Please try again later.",
            "not_found": "Suggestion not found.",
            "no_permission": "You don't have permission to manage suggestions.",
            "vote_failed": "Failed to update vote.",
        },
    },
}


class Suggestions(OakBranch):
    """Handles user suggestions with voting and management."""

    def __init__(self, ctx: BranchContext):
        super().__init__(ctx)
        self.db_path = get_db_path()

    async def on_enable(self) -> None:
        if self.db:
            await self.db.initialize(SUGGESTIONS_SCHEMA)
            # Migrate existing votes from JSON columns to votes table
            await self._migrate_votes()

        channel_id = self.setting("channel_id", default=0)
        if channel_id == 0:
            self.log.warning("suggestions channel_id is 0 (placeholder) — suggestions will not work")
        manager_role_ids = self.setting("manager_role_ids", default=[])
        if not manager_role_ids or manager_role_ids == [0]:
            self.log.warning("manager_role_ids is empty or placeholder — suggestion management will not work")

        self.log.info("Registering SuggestionVoteView for persistent interactions")
        self.bot.add_view(SuggestionVoteView(legacy=True))
        self.bot.add_view(SuggestionVoteView())

    async def _migrate_votes(self):
        """Backfill suggestion_votes table from legacy JSON columns."""
        try:
            row = await self.db.fetchone("SELECT COUNT(*) FROM suggestion_votes")
            if row and row[0] > 0:
                return  # Already migrated

            rows = await self.db.fetchall("SELECT id, likes, dislikes FROM suggestions")
            if not rows:
                return

            conn = self.db.connect()
            async with self.db.write_lock:
                for suggestion_id, likes_json, dislikes_json in rows:
                    likes = json.loads(likes_json) if likes_json else []
                    dislikes = json.loads(dislikes_json) if dislikes_json else []
                    for user_id in likes:
                        await conn.execute(
                            "INSERT OR IGNORE INTO suggestion_votes (suggestion_id, user_id, vote_type) VALUES (?, ?, 'like')",
                            (suggestion_id, user_id),
                        )
                    for user_id in dislikes:
                        await conn.execute(
                            "INSERT OR IGNORE INTO suggestion_votes (suggestion_id, user_id, vote_type) VALUES (?, ?, 'dislike')",
                            (suggestion_id, user_id),
                        )
                await conn.commit()
            self.log.info("Migrated suggestion votes to new table")
        except Exception as e:
            self.log.error(f"Failed to migrate suggestion votes: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        channel_id = self.setting("channel_id", default=0)
        if message.channel.id != channel_id:
            return

        max_length = self.setting("validation", "max_length", default=4000)
        min_length = self.setting("validation", "min_length", default=10)
        content = sanitize_text(message.content, max_length=max_length)

        if not content:
            try:
                await message.author.send(self.setting("messages", "empty", default="Your suggestion was empty or invalid."))
            except discord.Forbidden:
                pass
            try:
                await message.delete()
            except discord.HTTPException:
                self.log.warning(f"Failed to delete empty suggestion message from {message.author}")
            return

        if len(content) < min_length:
            try:
                await message.author.send(self.setting("messages", "too_short", default="Your suggestion is too short."))
            except discord.Forbidden:
                pass
            try:
                await message.delete()
            except discord.HTTPException:
                self.log.warning(f"Failed to delete short suggestion message from {message.author}")
            return

        sent = None
        thread = None
        try:
            color_pending = self.setting("ui", "embed_colors", "pending", default=0x2B2D31)

            # Sanitize suggestion content for embed display
            sanitized_content = discord.utils.escape_markdown(discord.utils.escape_mentions(content))

            embed = discord.Embed(title="💡 New Pending Suggestion", color=color_pending)
            embed.set_thumbnail(url=message.author.display_avatar.url)
            embed.add_field(name="💬 Suggestion", value=truncate(sanitized_content), inline=False)
            embed.add_field(name="📊 Statistics", value="**0** Likes\n**0** Dislikes\nStatus: **Pending**", inline=True)
            embed.add_field(name="👤 Author", value=f"{message.author.mention} ({message.author.name})", inline=True)

            view = SuggestionVoteView()
            sent = await message.channel.send(embed=embed, view=view)

            title_max = self.setting("ui", "thread", "title_max_length", default=40)
            title_prefix = self.setting("ui", "thread", "title_prefix", default="💬 Discussion: ")
            raw_title = sanitize_text(content.strip()[:title_max], max_length=title_max)
            thread_title = f"{title_prefix}{raw_title}" if raw_title else f"{title_prefix}{message.author.display_name}"
            thread = await sent.create_thread(name=thread_title)

            if self.db:
                try:
                    await self.db.execute(
                        """INSERT INTO suggestions (message_id, thread_id, user_id, content, likes, dislikes, status, reason)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (sent.id, thread.id, message.author.id, content, json.dumps([]), json.dumps([]), "Pending", None),
                    )
                except Exception as db_err:
                    self.log.error(f"Failed to insert suggestion into DB: {db_err}")
                    # Clean up the message and thread since DB insert failed
                    try:
                        await thread.delete()
                    except discord.HTTPException:
                        pass
                    try:
                        await sent.delete()
                    except discord.HTTPException:
                        pass
                    try:
                        await message.author.send(self.setting("messages", "created_error", default="Failed to create your suggestion."))
                    except discord.Forbidden:
                        pass
                    return

            try:
                await message.delete()
            except discord.HTTPException:
                self.log.warning(f"Failed to delete original suggestion message from {message.author}")

            self.log.info(f"New suggestion from {message.author} (ID: {message.author.id})")

        except discord.HTTPException as e:
            self.log.error(f"Failed to create suggestion: {e}")
            try:
                await message.author.send(self.setting("messages", "created_error", default="Failed to create your suggestion."))
            except discord.Forbidden:
                pass
        except Exception as e:
            self.log.error(f"Unexpected error creating suggestion: {e}")
            try:
                await message.author.send("An error occurred while creating your suggestion.")
            except discord.Forbidden:
                pass
