"""Suggestions branch — user suggestions with voting and management."""

import json

import discord
from discord import app_commands
from discord.ext import commands

from oak import OakBranch
from oak.constants import THREAD_NAME_MAX
from oak.context import BranchContext
from oak.utils import sanitize_text, truncate_for_embed_field
from oak.views import PaginatedEmbedView

from .helpers import truncate
from .views import SuggestionVoteView, configure as configure_views

SUGGESTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER UNIQUE,
    thread_id INTEGER,
    user_id INTEGER,
    content TEXT,
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
        self._registered_views: list = []

    async def on_enable(self) -> None:
        if self.db:
            await self.db.initialize(SUGGESTIONS_SCHEMA)
            # Migrate existing votes from JSON columns to votes table,
            # then retire the columns they came from.
            await self._migrate_votes()
            await self._drop_legacy_vote_columns()

        # Set module-level refs for views/handlers/modals
        configure_views(self.db, self.config)

        channel_id = self.setting("channel_id", default=0)
        if channel_id == 0:
            self.log.warning("suggestions channel_id is 0 (placeholder) — suggestions will not work")
        manager_role_ids = self.setting("manager_role_ids", default=[])
        if not manager_role_ids or manager_role_ids == [0]:
            self.log.warning("manager_role_ids is empty or placeholder — suggestion management will not work")

        # Track views so on_disable() can stop their callbacks on /reload
        self.log.info("Registering SuggestionVoteView for persistent interactions")
        self._registered_views = [SuggestionVoteView(legacy=True), SuggestionVoteView()]
        for view in self._registered_views:
            self.bot.add_view(view)

    async def on_disable(self) -> None:
        """Stop persistent views so stale callbacks don't survive a /reload."""
        for view in self._registered_views:
            view.stop()
        self._registered_views.clear()

    async def _has_legacy_vote_columns(self) -> bool:
        """True if the pre-``suggestion_votes`` JSON columns are still present."""
        cols = {row[1] for row in await self.db.fetchall("PRAGMA table_info(suggestions)")}
        return "likes" in cols or "dislikes" in cols

    async def _drop_legacy_vote_columns(self) -> None:
        """Drop the superseded ``likes``/``dislikes`` JSON columns.

        Runs after ``_migrate_votes`` so anything they held is already in
        ``suggestion_votes``. Guarded on the columns actually existing rather
        than expressed as a framework Migration, because SQLite has no
        conditional DDL and a plain DROP would fail on a fresh database that
        never had them.

        Requires SQLite >= 3.35 for ALTER TABLE DROP COLUMN; older versions
        just leave the columns in place rather than failing the branch load.
        """
        try:
            if not await self._has_legacy_vote_columns():
                return

            # Refuse to drop while anything still only exists in the old
            # columns — better to keep dead columns than lose votes.
            leftover = await self.db.fetchone(
                """SELECT COUNT(*) FROM suggestions s
                   WHERE (s.likes IS NOT NULL AND s.likes NOT IN ('', '[]'))
                      OR (s.dislikes IS NOT NULL AND s.dislikes NOT IN ('', '[]'))"""
            )
            if leftover and leftover[0]:
                votes_row = await self.db.fetchone("SELECT COUNT(*) FROM suggestion_votes")
                if not (votes_row and votes_row[0]):
                    self.log.warning(
                        "Legacy vote columns still hold data and suggestion_votes is empty; "
                        "leaving the columns in place"
                    )
                    return

            for column in ("likes", "dislikes"):
                try:
                    # Column names are literals from this function, not input.
                    await self.db.execute(f"ALTER TABLE suggestions DROP COLUMN {column}")
                    self.log.info(f"Dropped legacy suggestions.{column} column")
                except Exception as e:
                    self.log.warning(f"Could not drop suggestions.{column}: {e}")
                    return
        except Exception as e:
            self.log.error(f"Error dropping legacy vote columns: {e}", exc_info=True)

    async def _migrate_votes(self):
        """Backfill suggestion_votes table from legacy JSON columns.

        Per-row try/except so a single malformed legacy payload doesn't
        abort the whole migration and lose votes for every other suggestion.
        """
        try:
            # A fresh database never had the legacy columns, and an already
            # cleaned one has had them dropped. Either way there's nothing to
            # backfill, and selecting them would raise.
            if not await self._has_legacy_vote_columns():
                return

            row = await self.db.fetchone("SELECT COUNT(*) FROM suggestion_votes")
            if row and row[0] > 0:
                return  # Already migrated

            rows = await self.db.fetchall("SELECT id, likes, dislikes FROM suggestions")
            if not rows:
                return

            def _parse_user_ids(payload: str | None) -> list[int]:
                if not payload:
                    return []
                try:
                    data = json.loads(payload)
                except (json.JSONDecodeError, TypeError):
                    return []
                if not isinstance(data, list):
                    return []
                return [uid for uid in data if isinstance(uid, int)]

            skipped = 0
            async with self.db.transaction() as conn:
                for suggestion_id, likes_json, dislikes_json in rows:
                    try:
                        likes = _parse_user_ids(likes_json)
                        dislikes = _parse_user_ids(dislikes_json)
                    except Exception:
                        skipped += 1
                        continue
                    for user_id in likes:
                        await conn.execute(
                            "INSERT OR IGNORE INTO suggestion_votes (suggestion_id, user_id, vote_type) VALUES (?, ?, 'like')",
                            (suggestion_id, user_id),
                        )
                    for user_id in dislikes:
                        # Skip if the user is in both lists — likes wins (it was inserted first)
                        await conn.execute(
                            "INSERT OR IGNORE INTO suggestion_votes (suggestion_id, user_id, vote_type) VALUES (?, ?, 'dislike')",
                            (suggestion_id, user_id),
                        )
            if skipped:
                self.log.warning(f"Migrated suggestion votes (skipped {skipped} malformed rows)")
            else:
                self.log.info("Migrated suggestion votes to new table")
        except Exception as e:
            self.log.error(f"Failed to migrate suggestion votes: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        # Only act on guild messages in the configured suggestion channel.
        # Explicitly reject DMs and threads — otherwise a DM whose channel.id
        # somehow matched would have the bot try to delete a DM message.
        if message.guild is None or isinstance(message.channel, discord.Thread):
            return
        channel_id = self.setting("channel_id", default=0)
        if message.channel.id != channel_id:
            return

        max_length = self.setting("validation", "max_length", default=4000)
        min_length = self.setting("validation", "min_length", default=10)
        content = sanitize_text(message.content, max_length=max_length)

        # Allow only the author's mention in notices so a forged @everyone in
        # the suggestion content can't ride through the validation reply.
        notice_mentions = discord.AllowedMentions(
            users=[message.author], roles=False, everyone=False
        )

        if not content:
            try:
                await message.delete()
            except discord.HTTPException:
                self.log.warning(f"Failed to delete empty suggestion message from {message.author}")
            try:
                await message.channel.send(
                    f"{message.author.mention} {self.setting('messages', 'empty', default='Your suggestion was empty or invalid.')}",
                    delete_after=10,
                    allowed_mentions=notice_mentions,
                )
            except discord.HTTPException:
                pass
            return

        if len(content) < min_length:
            try:
                await message.delete()
            except discord.HTTPException:
                self.log.warning(f"Failed to delete short suggestion message from {message.author}")
            try:
                await message.channel.send(
                    f"{message.author.mention} {self.setting('messages', 'too_short', default='Your suggestion is too short.')}",
                    delete_after=10,
                    allowed_mentions=notice_mentions,
                )
            except discord.HTTPException:
                pass
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
            # m1: Clamp thread name to Discord's limit
            thread_title = thread_title[:THREAD_NAME_MAX]
            thread = await sent.create_thread(name=thread_title)

            if self.db:
                try:
                    await self.db.execute(
                        """INSERT INTO suggestions (message_id, thread_id, user_id, content, status)
                           VALUES (?, ?, ?, ?, ?)""",
                        (sent.id, thread.id, message.author.id, content, "Pending"),
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

    # ------------------------------------------------------------------
    # /topsuggestions
    # ------------------------------------------------------------------

    @app_commands.command(
        name="topsuggestions",
        description="View the highest-voted suggestions",
    )
    @app_commands.describe(
        status="Filter by status (default: All)",
        sort="Sort order (default: Net votes)",
    )
    @app_commands.choices(status=[
        app_commands.Choice(name="All", value="all"),
        app_commands.Choice(name="Pending", value="Pending"),
        app_commands.Choice(name="Approved", value="Approved"),
        app_commands.Choice(name="Denied", value="Denied"),
    ])
    @app_commands.choices(sort=[
        app_commands.Choice(name="Net votes (likes - dislikes)", value="net"),
        app_commands.Choice(name="Most likes", value="likes"),
        app_commands.Choice(name="Most dislikes", value="dislikes"),
    ])
    async def topsuggestions(
        self,
        interaction: discord.Interaction,
        status: str = "all",
        sort: str = "net",
    ):
        """Show a leaderboard of the highest-voted suggestions."""
        await interaction.response.defer(ephemeral=True)

        try:
            # The aggregates and the legacy `suggestions.likes`/`dislikes` text
            # columns share names; some SQLite versions resolve ORDER BY to the
            # base column instead of the alias, which would sort lexicographically
            # on JSON text. Use the explicit aggregate expression so the order
            # is unambiguous regardless of resolution order.
            like_agg = "COALESCE(SUM(CASE WHEN sv.vote_type='like' THEN 1 ELSE 0 END), 0)"
            dislike_agg = "COALESCE(SUM(CASE WHEN sv.vote_type='dislike' THEN 1 ELSE 0 END), 0)"
            sort_map = {
                "net": f"({like_agg} - {dislike_agg}) DESC",
                "likes": f"{like_agg} DESC",
                "dislikes": f"{dislike_agg} DESC",
            }
            order_clause = sort_map.get(sort, sort_map["net"])

            if status != "all":
                query = f"""
                    SELECT s.id, s.message_id, s.user_id, s.content, s.status,
                           {like_agg} AS likes,
                           {dislike_agg} AS dislikes
                    FROM suggestions s
                    LEFT JOIN suggestion_votes sv ON sv.suggestion_id = s.id
                    WHERE s.status = ?
                    GROUP BY s.id
                    ORDER BY {order_clause}
                    LIMIT 50
                """
                rows = await self.db.fetchall(query, (status,))
            else:
                query = f"""
                    SELECT s.id, s.message_id, s.user_id, s.content, s.status,
                           {like_agg} AS likes,
                           {dislike_agg} AS dislikes
                    FROM suggestions s
                    LEFT JOIN suggestion_votes sv ON sv.suggestion_id = s.id
                    GROUP BY s.id
                    ORDER BY {order_clause}
                    LIMIT 50
                """
                rows = await self.db.fetchall(query)

            if not rows:
                label = status if status != "all" else "any"
                await interaction.followup.send(
                    f"No suggestions found with status: **{label}**.",
                    ephemeral=True,
                )
                return

            # Build paginated embeds (5 per page)
            channel_id = self.setting("channel_id", default=0)
            guild_id = interaction.guild_id
            per_page = 5

            sort_labels = {"net": "Net Votes", "likes": "Most Likes", "dislikes": "Most Dislikes"}
            status_label = status if status != "all" else "All"
            title = f"Top Suggestions — {status_label} — {sort_labels.get(sort, sort)}"

            pages = []
            for page_start in range(0, len(rows), per_page):
                page_rows = rows[page_start:page_start + per_page]
                page_num = (page_start // per_page) + 1
                total_pages = -(-len(rows) // per_page)

                embed = discord.Embed(title=title, color=self.setting("ui", "embed_colors", "pending", default=0x2B2D31))

                for i, (sid, msg_id, user_id, content, s_status, likes, dislikes) in enumerate(page_rows, start=page_start + 1):
                    net = likes - dislikes
                    preview = truncate_for_embed_field(content or "*(no content)*", max_length=80)
                    jump = f"https://discord.com/channels/{guild_id}/{channel_id}/{msg_id}" if msg_id and channel_id else ""
                    jump_text = f" — [Jump]({jump})" if jump else ""

                    embed.add_field(
                        name=f"#{i} — {s_status}",
                        value=(
                            f"{preview}\n"
                            f"By <@{user_id}> — "
                            f"\U0001f44d {likes}  \U0001f44e {dislikes}  (net: {net:+d})"
                            f"{jump_text}"
                        ),
                        inline=False,
                    )

                embed.set_footer(text=f"Page {page_num}/{total_pages} — {len(rows)} suggestions")
                pages.append(embed)

            if len(pages) == 1:
                await interaction.followup.send(embed=pages[0], ephemeral=True)
            else:
                view = PaginatedEmbedView(pages, interaction.user.id)
                message = await interaction.followup.send(embed=pages[0], view=view, ephemeral=True)
                view.message = message

        except Exception as e:
            self.log.error(f"Error in /topsuggestions command: {e}", exc_info=True)
            await interaction.followup.send(
                "An error occurred while fetching the leaderboard.",
                ephemeral=True,
            )
