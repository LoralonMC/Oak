"""
Application Branch - Main Module
Manages staff application workflow with multi-page forms, background checks, and approval system.
"""

import discord
from sqlite3 import IntegrityError
from discord import app_commands
from discord.ext import tasks
from oak import OakBranch
from oak.context import BranchContext
from oak.database import Migration

# Import our modularized components
from .helpers import (
    get_application_questions,
    get_embed_colors,
    get_message,
    is_staff,
)
from .views import (
    configure as configure_views,
    STATUS_EMOJI,
    ApplicationButtonView,
    StartCancelView,
    ContinueView,
    PostSubmissionView,
    ManageView
)

DEFAULT_CONFIG = {
    "enabled": True,
    "settings": {
        "application": {
            "position_name": "Staff Member",
            "button_label": "Apply for Staff",
            "channel_name_prefix": "application",
        },
        "application_channel_id": 0,
        "application_category_id": 0,
        "accepted_category_id": 0,
        "admin_chat_id": 0,
        "punishment_forum_channel_id": 0,
        "reviewer_role_ids": [],
        "required_link_role_id": 0,
        "inactivity": {
            "enabled": True,
            "check_interval_hours": 12,
            "warning_after_days": 3,
            "abandon_after_days": 7,
        },
        "denial": {
            "delete_delay_seconds": 10,
            "auto_delete_no_dm": True,
            "auto_delete_no_dm_after_hours": 24,
        },
        "mysql": {
            "enabled": False,
        },
        "ui": {
            "embed_colors": {
                "info": 0x5865F2,
                "success": 0x57F287,
                "warning": 0xFEE75C,
                "error": 0xED4245,
            },
        },
        "questions": [
            {"label": "What is your username?", "max_length": 50},
            {"label": "What is your age?", "max_length": 20},
            {"label": "How long have you been part of the community?", "max_length": 100},
            {"label": "Why do you want to join the staff team?", "max_length": 1000},
        ],
    },
}

# Database schema for applications
# NOTE: New columns (last_activity_at, warning_sent_at, denied_at, denial_dm_sent, denial_reason) are added via migration in on_enable
APPLICATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    channel_id INTEGER UNIQUE,
    app_index INTEGER,
    answers TEXT,
    status TEXT,
    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
"""


async def handle_application_start(interaction: discord.Interaction):
    """
    Handle the start of a new application.

    Uses a 3-phase approach to avoid holding a DB connection during slow Discord API calls:
      Phase 1: Check existing apps (short DB read)
      Phase 2: Create Discord channel (no DB held)
      Phase 3: Save to DB (atomic app_index assignment)

    Args:
        interaction: Discord interaction from the Apply button
    """
    from .views import _db, _config

    user = interaction.user
    guild = interaction.guild
    colors = get_embed_colors(_config)

    try:
        # --- Phase 1: Check existing applications ---
        row = await _db.fetchone(
            "SELECT channel_id, status FROM applications WHERE user_id = ? AND status IN ('in_progress', 'pending')",
            (user.id,)
        )
        if row:
            channel_id, status = row
            existing_channel = guild.get_channel(channel_id)
            if existing_channel:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="You already have an open application!",
                        description=f"Please continue your application here: {existing_channel.mention}\n\nStatus: **{status.title()}**",
                        color=colors["warning"]
                    ),
                    ephemeral=True
                )
                return
            else:
                # Channel was deleted but application still exists - clean it up
                await _db.execute("UPDATE applications SET status = 'cancelled' WHERE channel_id = ?", (channel_id,))

        # --- Phase 2: Create Discord channel (no DB held - this is the slow Discord API call) ---
        application_category_id = _config.get("settings", {}).get("application_category_id", 0)
        channel_name_prefix = _config.get("settings", {}).get("application", {}).get("channel_name_prefix", "application")

        # Create channel with proper permissions
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
                manage_channels=True
            ),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=False,  # Prevent spam/abuse
                add_reactions=False
            ),
        }

        # Add reviewer roles with management permissions
        reviewer_role_ids = _config.get("settings", {}).get("reviewer_role_ids", [])
        for role_id in reviewer_role_ids:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                    manage_threads=True
                )

        category = discord.utils.get(guild.categories, id=application_category_id)
        if not category:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="Configuration Error",
                    description="Application system is not properly configured. Please contact an administrator.",
                    color=colors["error"]
                ),
                ephemeral=True
            )
            return

        # Use a temporary name; we'll know the real index after the atomic INSERT
        channel = await guild.create_text_channel(
            name=f"{channel_name_prefix}-new",
            category=category,
            overwrites=overwrites,
            reason=f"Application created by {user}"
        )

        # --- Phase 3: Save to DB (atomic app_index assignment) ---
        try:
            async with _db.transaction() as conn:
                # Atomic app_index assignment: INSERT with subquery to avoid separate SELECT + INSERT race
                await conn.execute(
                    """INSERT INTO applications (user_id, channel_id, app_index, answers, status, submitted_at, last_activity_at)
                    VALUES (?, ?, (SELECT COALESCE(MAX(app_index), 0) + 1 FROM applications), ?, ?, datetime('now'), datetime('now'))""",
                    (user.id, channel.id, "[]", "in_progress")
                )

                # Retrieve the assigned app_index for channel renaming
                cursor = await conn.execute(
                    "SELECT app_index FROM applications WHERE channel_id = ?",
                    (channel.id,)
                )
                idx_row = await cursor.fetchone()
                next_index = idx_row[0] if idx_row else 0

            # Rename channel to include the real index
            try:
                await channel.edit(name=f"{channel_name_prefix}-{next_index:02}")
            except discord.HTTPException:
                pass  # Non-critical: channel works even with temp name

        except IntegrityError:
            # Race condition: user already has an application
            await channel.delete(reason="Duplicate application (race condition)")

            # Find existing application
            existing = await _db.fetchone(
                "SELECT channel_id, status FROM applications WHERE user_id = ? AND status IN ('in_progress', 'pending')",
                (user.id,)
            )

            if existing:
                existing_channel_id, existing_status = existing
                existing_channel = guild.get_channel(existing_channel_id)
                if existing_channel:
                    await interaction.followup.send(
                        embed=discord.Embed(
                            title="Application Already Exists",
                            description=f"You already have an application: {existing_channel.mention}\n\nStatus: **{existing_status.title()}**",
                            color=colors["warning"]
                        ),
                        ephemeral=True
                    )
                    return

            # If we get here, something went wrong
            raise

        # Try to DM the user
        try:
            await user.send(embed=discord.Embed(
                title="Application Started",
                description=f"Your application channel is {channel.mention}.",
                color=colors["success"]
            ))
        except discord.Forbidden:
            await channel.send(embed=discord.Embed(
                description=":warning: Couldn't DM applicant. Please remind them to open DMs.",
                color=colors["warning"]
            ))
        except Exception:
            pass

        # Send welcome message in application channel
        welcome_title, welcome_description = get_message(
            _config,
            "welcome",
            "Welcome to the Application Process",
            (
                "Use the buttons below to begin your application or cancel if you changed your mind.\n\n"
                "**Before you start:**\n"
                "- Answer all questions honestly and thoroughly\n"
                "- Your progress is saved after each page\n\n"
                "Good luck!"
            ),
        )
        await channel.send(
            content=user.mention,
            embed=discord.Embed(
                title=welcome_title,
                description=welcome_description,
                color=colors["info"]
            ),
            view=StartCancelView()
        )

        # Confirm to user
        await interaction.followup.send(
            embed=discord.Embed(
                title="Application Channel Created!",
                description=f"Your application channel is ready: {channel.mention}\n\nHead there to start your application.",
                color=colors["success"]
            ),
            ephemeral=True
        )

    except discord.HTTPException:
        try:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="Error Creating Application",
                    description="Failed to create your application channel. Please try again later or contact an administrator.",
                    color=colors["error"]
                ),
                ephemeral=True
            )
        except Exception:
            pass

    except Exception:
        try:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="Error",
                    description="An unexpected error occurred. Please contact an administrator.",
                    color=colors["error"]
                ),
                ephemeral=True
            )
        except Exception:
            pass


class Application(OakBranch):
    """Staff application management system."""

    def __init__(self, ctx: BranchContext):
        super().__init__(ctx)

        # Cache frequently used settings
        settings = self.config.get("settings", {})
        self.application_channel_id = settings.get("application_channel_id", 0)
        self.application_category_id = settings.get("application_category_id", 0)
        self.accepted_category_id = settings.get("accepted_category_id", 0)
        self.admin_chat_id = settings.get("admin_chat_id", 0)
        self.punishment_forum_channel_id = settings.get("punishment_forum_channel_id", 0)
        self.required_link_role_id = settings.get("required_link_role_id", 0)

        # Load UI settings
        ui_settings = settings.get("ui", {})
        embed_colors = ui_settings.get("embed_colors", {})
        self.color_info = embed_colors.get("info", 0x5865F2)       # Blurple
        self.color_success = embed_colors.get("success", 0x57F287)  # Green
        self.color_warning = embed_colors.get("warning", 0xFEE75C)  # Yellow
        self.color_error = embed_colors.get("error", 0xED4245)      # Red

        # Application button view
        self._application_button_view = ApplicationButtonView(handle_application_start_func=handle_application_start)
        self._registered_views: list = []

        self.log.info("Application branch initialized")

    async def on_enable(self):
        """Initialize database and register persistent views."""
        await self.db.initialize(APPLICATIONS_SCHEMA)

        # Configure module-level state for views/modals
        configure_views(self.db, self.config)

        # Warn if reviewer_role_ids is still the placeholder value
        reviewer_role_ids = self.config.get("settings", {}).get("reviewer_role_ids", [])
        if reviewer_role_ids == [0]:
            self.log.warning(
                "reviewer_role_ids is set to [0] (placeholder). "
                "Application reviews will not work until valid role IDs are configured."
            )

        # Run database migrations
        await self.db.migrate([
            Migration(
                name="add_last_activity_at",
                sql=(
                    "ALTER TABLE applications ADD COLUMN last_activity_at TIMESTAMP;\n"
                    "UPDATE applications SET last_activity_at = submitted_at;\n"
                    "CREATE INDEX IF NOT EXISTS idx_applications_last_activity ON applications(last_activity_at)"
                )
            ),
            Migration(
                name="add_warning_sent_at",
                sql="ALTER TABLE applications ADD COLUMN warning_sent_at TIMESTAMP"
            ),
            Migration(
                name="add_denied_at",
                sql="ALTER TABLE applications ADD COLUMN denied_at TIMESTAMP"
            ),
            Migration(
                name="add_denial_dm_sent",
                sql="ALTER TABLE applications ADD COLUMN denial_dm_sent INTEGER DEFAULT 0"
            ),
            Migration(
                name="add_denial_reason",
                sql="ALTER TABLE applications ADD COLUMN denial_reason TEXT"
            ),
            Migration(
                name="add_user_id_index",
                sql="CREATE INDEX IF NOT EXISTS idx_applications_user_id ON applications(user_id)"
            ),
            # Make the in-memory _creating_users dedupe authoritative at the DB
            # layer. A user can have at most one active application at a time;
            # cancelled/abandoned/denied/accepted rows are unconstrained so a
            # user can reapply after their previous attempt closes out.
            Migration(
                name="add_active_application_unique",
                sql=(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_active_unique "
                    "ON applications(user_id) WHERE status IN ('in_progress', 'pending')"
                ),
            ),
            Migration(
                name="add_cleanup_completed_at",
                sql="ALTER TABLE applications ADD COLUMN cleanup_completed_at TIMESTAMP",
            ),
        ])

        if self.application_channel_id == 0:
            self.log.warning("application_channel_id is 0 (placeholder) — application button will not be posted")
        if self.application_category_id == 0:
            self.log.warning("application_category_id is 0 (placeholder) — applications cannot be created")

        # Validate inactivity settings
        inactivity_config = self.config.get("settings", {}).get("inactivity", {})
        warning_days = inactivity_config.get("warning_after_days", 3)
        abandon_days = inactivity_config.get("abandon_after_days", 7)
        if abandon_days <= warning_days:
            self.log.error(
                f"abandon_after_days ({abandon_days}) must be greater than warning_after_days ({warning_days})"
            )

        # Register persistent views (both legacy and namespaced).
        # Track them so on_disable() can stop their callbacks on /reload —
        # otherwise stale closures keep pointing at the old DB/config refs.
        self.log.info("Registering persistent views for Application")
        app_button_legacy = ApplicationButtonView(handle_application_start_func=handle_application_start, legacy=True)
        self._registered_views = [
            app_button_legacy,
            self._application_button_view,
            StartCancelView(legacy=True),
            StartCancelView(),
            ContinueView(legacy=True),
            ContinueView(),
            PostSubmissionView(legacy=True),
            PostSubmissionView(),
            ManageView(legacy=True),
            ManageView(),
        ]
        for view in self._registered_views:
            self.bot.add_view(view)

        # Start inactivity check task if enabled
        inactivity_config = self.config.get("settings", {}).get("inactivity", {})
        if inactivity_config.get("enabled", True):
            check_interval = inactivity_config.get("check_interval_hours", 12)
            self.check_inactive_applications.change_interval(hours=check_interval)
            self.check_inactive_applications.start()
            self.register_task("check_inactive_applications", self.check_inactive_applications)
            self.log.info(f"Inactivity check task started (interval: {check_interval} hours)")

    async def on_disable(self):
        """Stop background tasks and tear down persistent views on unload."""
        if self.check_inactive_applications.is_running():
            self.check_inactive_applications.cancel()
        for view in self._registered_views:
            view.stop()
        self._registered_views.clear()
        self.log.info("Application branch unloaded")

    async def on_ready(self):
        """Ensure application message exists when bot is ready."""
        await self.ensure_application_message()
        self._warn_if_accepted_category_is_permissive()
        self.log.info("Application branch ready")

    def _warn_if_accepted_category_is_permissive(self) -> None:
        """Emit a warning if accepted_category_id grants @everyone view access.

        Moving an application channel into that category inherits its
        permission overwrites — a misconfigured destination would silently
        expose confidential application answers to the whole server.
        """
        if not self.accepted_category_id:
            return
        guild = self.bot.get_guild(self.bot.guild_id)
        if not guild:
            return
        category = guild.get_channel(self.accepted_category_id)
        if category is None or not isinstance(category, discord.CategoryChannel):
            return
        default_overwrite = category.overwrites_for(guild.default_role)
        if default_overwrite.view_channel is True:
            self.log.warning(
                f"accepted_category_id {self.accepted_category_id} grants "
                f"@everyone view_channel — moving applications there will expose "
                "their contents. Set a restrictive @everyone overwrite on that category."
            )

    @app_commands.command(name="appstats", description="Show application statistics")
    async def application_stats(self, interaction: discord.Interaction):
        """Show application statistics (Staff only)"""
        try:
            colors = get_embed_colors(self.config)
            # Check permissions
            reviewer_role_ids = self.config.get("settings", {}).get("reviewer_role_ids", [])

            if not is_staff(interaction.user, reviewer_role_ids):
                await interaction.response.send_message(
                    embed=discord.Embed(
                        description="You don't have permission to use this command.",
                        color=colors["error"]
                    ),
                    ephemeral=True
                )
                return

            # Get total applications
            total_row = await self.db.fetchone("SELECT COUNT(*) FROM applications")
            total = total_row[0]

            # Get status breakdown
            status_rows = await self.db.fetchall(
                "SELECT status, COUNT(*) FROM applications GROUP BY status"
            )
            status_counts = {row[0]: row[1] for row in status_rows}

            # Get recent applications (last 7 days)
            recent_row = await self.db.fetchone(
                "SELECT COUNT(*) FROM applications WHERE submitted_at >= datetime('now', '-7 days')"
            )
            recent = recent_row[0]

            # Get average processing time
            avg_row = await self.db.fetchone(
                "SELECT AVG(julianday(datetime('now')) - julianday(submitted_at)) FROM applications WHERE status IN ('accepted', 'denied')"
            )
            avg_processing = avg_row[0] if avg_row and avg_row[0] else 0

            embed = discord.Embed(
                title="Application Statistics",
                color=colors["info"]
            )

            embed.add_field(name="Total Applications", value=f"**{total}**", inline=True)
            embed.add_field(name="Last 7 Days", value=f"**{recent}**", inline=True)
            embed.add_field(name="Avg. Processing Time", value=f"**{avg_processing:.1f}** days", inline=True)

            status_text = "\n".join([
                f"**{status.title()}:** {count}"
                for status, count in sorted(status_counts.items())
            ])

            embed.add_field(name="Status Breakdown", value=status_text or "No data", inline=False)

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            self.log.error(f"Error getting application stats: {e}")
            await interaction.response.send_message("Failed to retrieve statistics.", ephemeral=True)

    @app_commands.command(name="apphistory", description="View a user's application history")
    @app_commands.describe(user="The user whose application history you want to view")
    async def application_history(self, interaction: discord.Interaction, user: discord.Member):
        """View a user's application history (Staff only)"""
        from .views import ApplicationHistoryView

        try:
            colors = get_embed_colors(self.config)
            # Check permissions
            reviewer_role_ids = self.config.get("settings", {}).get("reviewer_role_ids", [])

            if not is_staff(interaction.user, reviewer_role_ids):
                await interaction.response.send_message(
                    embed=discord.Embed(
                        description="You don't have permission to use this command.",
                        color=colors["error"]
                    ),
                    ephemeral=True
                )
                return

            # Fetch all applications for this user
            all_apps = await self.db.fetchall("""
                SELECT app_index, status, submitted_at, answers, channel_id, denied_at, denial_reason
                FROM applications
                WHERE user_id = ?
                ORDER BY submitted_at DESC
                LIMIT 10
            """, (user.id,))

            if not all_apps:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="Application History",
                        description=f"{user.mention} has no applications on record.",
                        color=colors["info"]
                    ),
                    ephemeral=True
                )
                return

            # Create summary embed
            summary_embed = discord.Embed(
                title=f"Application History: {user.display_name}",
                description=f"Found **{len(all_apps)}** application(s). Use the dropdown to view full details.",
                color=colors["info"]
            )
            summary_embed.set_thumbnail(url=user.display_avatar.url)

            for app_index, status, submitted_at, answers_json, channel_id, denied_at, denial_reason in all_apps:
                field_value = f"**Status:** {status.title()}\n**Date:** {submitted_at[:10]}"

                # Add denial reason if available
                if status == "denied" and denial_reason:
                    field_value += f"\n**Reason:** {denial_reason[:100]}{'...' if len(denial_reason) > 100 else ''}"

                summary_embed.add_field(
                    name=f"Application #{app_index}",
                    value=field_value,
                    inline=True
                )

            # Send with dropdown
            await interaction.response.send_message(
                embed=summary_embed,
                view=ApplicationHistoryView(user.id, all_apps),
                ephemeral=True
            )

        except Exception as e:
            self.log.error(f"Error getting application history: {e}")
            await interaction.response.send_message("Failed to retrieve application history.", ephemeral=True)

    async def ensure_application_message(self):
        """Ensure the application button message exists in the channel."""
        try:
            colors = get_embed_colors(self.config)
            channel = self.bot.get_channel(self.application_channel_id)
            if not channel:
                self.log.warning(f"Application channel {self.application_channel_id} not found")
                return

            history = [m async for m in channel.history(limit=50)]
            if not any(m.author == self.bot.user and m.components for m in history):
                panel_title, panel_description = get_message(
                    self.config,
                    "panel",
                    "Staff Application",
                    (
                        "Interested in becoming staff? Click below to start your application!\n\n"
                        "**Requirements:**\n"
                        "- Be an active member of the community\n"
                        "- Have a good understanding of server rules\n"
                        "- Be willing to help other players\n"
                        "- Have time to dedicate to staff duties"
                    ),
                )
                # Honor the configurable button label (the @button decorator hardcodes
                # a default; override it on the persistent view before posting).
                button_label = self.config.get("settings", {}).get("application", {}).get("button_label", "Apply for Staff")
                for child in self._application_button_view.children:
                    if getattr(child, "custom_id", None) == "oak:application:apply":
                        child.label = button_label
                await channel.send(
                    embed=discord.Embed(
                        title=panel_title,
                        description=panel_description,
                        color=colors["info"]
                    ),
                    view=self._application_button_view
                )
                self.log.info("Created new application button message")
        except Exception as e:
            self.log.error(f"Error ensuring application message: {e}")

    @tasks.loop(hours=12)
    async def check_inactive_applications(self):
        """Check for inactive applications and send warnings or mark as abandoned."""
        try:
            inactivity_config = self.config.get("settings", {}).get("inactivity", {})

            warning_days = inactivity_config.get("warning_after_days", 3)
            abandon_days = inactivity_config.get("abandon_after_days", 7)

            # Fix any NULL last_activity_at values (one-time cleanup for legacy apps)
            await self.db.execute(
                "UPDATE applications SET last_activity_at = submitted_at WHERE last_activity_at IS NULL"
            )

            # Find applications that need warnings (inactive for warning_days, no warning sent yet)
            # Exclude apps that should already be abandoned (inactive >= abandon_days)
            apps_needing_warning = await self.db.fetchall("""
                SELECT user_id, channel_id, last_activity_at
                FROM applications
                WHERE status = 'in_progress'
                AND warning_sent_at IS NULL
                AND julianday('now') - julianday(last_activity_at) >= ?
                AND julianday('now') - julianday(last_activity_at) < ?
            """, (warning_days, abandon_days))

            # Find applications that should be abandoned (inactive for abandon_days)
            apps_to_abandon = await self.db.fetchall("""
                SELECT user_id, channel_id, last_activity_at
                FROM applications
                WHERE status = 'in_progress'
                AND julianday('now') - julianday(last_activity_at) >= ?
            """, (abandon_days,))

            # Process warnings
            for user_id, channel_id, last_activity_at in apps_needing_warning:
                await self._send_inactivity_warning(user_id, channel_id, warning_days, abandon_days)

            # Process abandonments
            for user_id, channel_id, last_activity_at in apps_to_abandon:
                await self._abandon_application(user_id, channel_id)

            # Check for denied applications that need cleanup (where DM failed)
            denied_to_cleanup = await self._check_denied_apps_cleanup()

            if apps_needing_warning or apps_to_abandon or denied_to_cleanup:
                self.log.info(f"Inactivity check: Processed {len(apps_needing_warning)} warnings, {len(apps_to_abandon)} abandonments, and {denied_to_cleanup} denied app cleanups")

        except Exception as e:
            self.log.error(f"Error in check_inactive_applications: {e}", exc_info=True)

    @check_inactive_applications.before_loop
    async def before_check_inactive_applications(self):
        """Wait until bot is ready before starting task."""
        await self.bot.wait_until_ready()

    @check_inactive_applications.error
    async def check_inactive_applications_error(self, error: Exception):
        """Log unhandled errors in the inactivity check task."""
        self.log.error(f"Unhandled error in check_inactive_applications: {error}", exc_info=True)

    async def _send_inactivity_warning(self, user_id: int, channel_id: int, warning_days: int, abandon_days: int):
        """Send inactivity warning to user via DM and in channel."""
        try:
            colors = get_embed_colors(self.config)
            guild = self.bot.get_guild(self.bot.guild_id)
            if not guild:
                self.log.error(f"Guild {self.bot.guild_id} not found")
                return

            user = guild.get_member(user_id)
            channel = guild.get_channel(channel_id)

            if not channel:
                self.log.warning(f"Channel {channel_id} not found for warning")
                return

            days_remaining = abandon_days - warning_days

            # Get configurable messages
            inactivity_config = self.config.get("settings", {}).get("inactivity", {})

            # DM warning config
            dm_config = inactivity_config.get("warning_dm", {})
            dm_title = dm_config.get("title", "Application Inactivity Warning")
            dm_description = dm_config.get("description",
                "Your application has been inactive for **{warning_days} days**.\n\n"
                "**Please continue your application within the next {days_remaining} days** "
                "or it will be automatically closed and marked as abandoned.\n\n"
                "Click the button in {channel_mention} to continue."
            )

            # Format DM description with variables
            dm_description = dm_description.format(
                warning_days=warning_days,
                days_remaining=days_remaining,
                channel_mention=channel.mention if channel else 'your application channel'
            )

            warning_embed = discord.Embed(
                title=dm_title,
                description=dm_description,
                color=colors["warning"]
            )

            # Try to DM the user
            dm_sent = False
            if user:
                try:
                    await user.send(embed=warning_embed)
                    dm_sent = True
                    self.log.info(f"Sent inactivity warning DM to user {user_id}")
                except discord.Forbidden:
                    self.log.warning(f"Could not DM user {user_id} - DMs closed")
                except discord.HTTPException as e:
                    self.log.error(f"Failed to DM user {user_id}: {e}")

            # Send warning in channel
            if channel:
                try:
                    # Channel warning config
                    channel_config = inactivity_config.get("warning_channel", {})
                    channel_title = channel_config.get("title", "Inactivity Warning")
                    channel_description = channel_config.get("description",
                        "{user_mention}, your application has been inactive for **{warning_days} days**.\n\n"
                        "**Please continue within {days_remaining} days** or this application will be closed.\n\n"
                        "Click the button below to continue your application."
                    )
                    channel_footer = channel_config.get("footer", "Note: I couldn't DM you. Please enable DMs from server members.")

                    # Format channel description with variables
                    channel_description = channel_description.format(
                        user_mention=f"<@{user_id}>",
                        warning_days=warning_days,
                        days_remaining=days_remaining
                    )

                    channel_warning = discord.Embed(
                        title=channel_title,
                        description=channel_description,
                        color=colors["warning"]
                    )

                    if not dm_sent:
                        channel_warning.set_footer(text=channel_footer)

                    await channel.send(content=f"<@{user_id}>", embed=channel_warning)
                    self.log.info(f"Sent inactivity warning in channel {channel_id}")
                except discord.HTTPException as e:
                    self.log.error(f"Failed to send warning in channel {channel_id}: {e}")

            # Mark warning as sent
            await self.db.execute(
                "UPDATE applications SET warning_sent_at = datetime('now') WHERE channel_id = ?",
                (channel_id,)
            )

        except Exception as e:
            self.log.error(f"Error sending inactivity warning: {e}", exc_info=True)

    async def _abandon_application(self, user_id: int, channel_id: int):
        """Mark application as abandoned and delete channel."""
        try:
            colors = get_embed_colors(self.config)
            guild = self.bot.get_guild(self.bot.guild_id)
            if not guild:
                self.log.error(f"Guild {self.bot.guild_id} not found")
                return

            user = guild.get_member(user_id)
            channel = guild.get_channel(channel_id)

            # Update database
            await self.db.execute(
                "UPDATE applications SET status = 'abandoned' WHERE channel_id = ?",
                (channel_id,)
            )

            # Try to DM user
            if user:
                try:
                    # Get configurable abandonment message
                    inactivity_config = self.config.get("settings", {}).get("inactivity", {})
                    abandon_config = inactivity_config.get("abandon_dm", {})

                    abandon_title = abandon_config.get("title", "Application Abandoned")
                    abandon_description = abandon_config.get("description",
                        "Your application has been automatically closed due to inactivity.\n\n"
                        "You can start a new application at any time by clicking the application button again."
                    )

                    await user.send(
                        embed=discord.Embed(
                            title=abandon_title,
                            description=abandon_description,
                            color=colors["error"]
                        )
                    )
                    self.log.info(f"Sent abandonment DM to user {user_id}")
                except discord.Forbidden:
                    self.log.warning(f"Could not DM user {user_id} about abandonment")
                except discord.HTTPException as e:
                    self.log.error(f"Failed to DM user {user_id}: {e}")

            # Delete channel
            if channel:
                try:
                    await channel.delete(reason=f"Application abandoned due to inactivity (user: {user_id})")
                    self.log.info(f"Deleted abandoned application channel {channel_id} for user {user_id}")
                except discord.HTTPException as e:
                    self.log.error(f"Failed to delete channel {channel_id}: {e}")

        except Exception as e:
            self.log.error(f"Error abandoning application: {e}", exc_info=True)

    async def _check_denied_apps_cleanup(self):
        """Check for denied applications where DM failed and clean them up after configured time."""
        try:
            denial_config = self.config.get("settings", {}).get("denial", {})

            auto_delete_enabled = denial_config.get("auto_delete_no_dm", True)
            if not auto_delete_enabled:
                return 0  # Auto-delete disabled

            auto_delete_hours = denial_config.get("auto_delete_no_dm_after_hours", 24)

            # Find denied apps where DM failed, the time has expired, and we
            # haven't already completed cleanup for this row (so a channel
            # that's been manually deleted doesn't get rediscovered every
            # tick forever).
            apps_to_delete = await self.db.fetchall("""
                SELECT user_id, channel_id, denied_at
                FROM applications
                WHERE status = 'denied'
                AND denial_dm_sent = 0
                AND denied_at IS NOT NULL
                AND cleanup_completed_at IS NULL
                AND (julianday('now') - julianday(denied_at)) * 24 >= ?
            """, (auto_delete_hours,))

            if not apps_to_delete:
                return 0

            guild = self.bot.get_guild(self.bot.guild_id)
            if not guild:
                self.log.error(f"Guild {self.bot.guild_id} not found")
                return 0

            deleted_count = 0
            for user_id, channel_id, denied_at in apps_to_delete:
                channel = guild.get_channel(channel_id)
                channel_gone = channel is None
                if channel:
                    try:
                        await channel.delete(reason=f"Denied application auto-cleanup (DM failed, {auto_delete_hours}h elapsed)")
                        self.log.info(f"Auto-deleted denied application channel {channel_id} for user {user_id} (DM failed, waited {auto_delete_hours}h)")
                        deleted_count += 1
                        channel_gone = True
                    except discord.NotFound:
                        channel_gone = True
                    except discord.HTTPException as e:
                        self.log.error(f"Failed to auto-delete denied channel {channel_id}: {e}")

                if channel_gone:
                    # Mark the row so we don't keep finding it every 12h.
                    try:
                        await self.db.execute(
                            "UPDATE applications SET cleanup_completed_at = datetime('now') WHERE channel_id = ?",
                            (channel_id,),
                        )
                    except Exception as e:
                        self.log.error(f"Failed to record cleanup for {channel_id}: {e}")

            return deleted_count

        except Exception as e:
            self.log.error(f"Error checking denied apps cleanup: {e}", exc_info=True)
            return 0
