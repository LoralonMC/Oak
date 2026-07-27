"""
Tickets Branch - Main Module
Thread-based support ticket system with category management.
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
from datetime import datetime, timedelta, timezone
from sqlite3 import IntegrityError
import time

from pathlib import Path

from oak import OakBranch
from oak.context import BranchContext
from oak.database import Migration

from .helpers import (
    get_embed_colors,
    is_staff,
    can_manage_ticket_category,
    hash_config,
    validate_config,
    format_log_embed,
    get_next_ticket_number
)
from .views import (
    TicketPanelView,
    TicketControlView,
    LegacyReminderView,
    ReminderStopButton,
    ReminderSnooze1hButton,
    ReminderSnooze6hButton,
    ReminderSnooze1dButton,
    build_reminder_view,
    configure as configure_views,
)

TICKETS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    ticket_number INTEGER,
    status TEXT NOT NULL,
    closed_by INTEGER,
    close_reason TEXT,
    reopened_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_category ON tickets(category);
CREATE INDEX IF NOT EXISTS idx_tickets_user_category ON tickets(user_id, category);
CREATE INDEX IF NOT EXISTS idx_tickets_created_at ON tickets(created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_category_number
ON tickets(category, ticket_number) WHERE ticket_number IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_user_category_open
ON tickets(user_id, category) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS panel_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER UNIQUE,
    channel_id INTEGER,
    config_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ticket_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_thread_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    initial_reminder_at TIMESTAMP,
    last_reminded_at TIMESTAMP,
    last_reminder_message_id INTEGER,
    daily_reminder_enabled INTEGER DEFAULT 1,
    dm_enabled INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ticket_thread_id) REFERENCES tickets(thread_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reminders_active ON ticket_reminders(active);
CREATE INDEX IF NOT EXISTS idx_reminders_thread ON ticket_reminders(ticket_thread_id);
CREATE INDEX IF NOT EXISTS idx_reminders_user ON ticket_reminders(user_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reminders_active_unique
ON ticket_reminders(ticket_thread_id, user_id) WHERE active = 1;
"""


class Tickets(OakBranch):
    """Thread-based support ticket system."""

    def __init__(self, ctx: BranchContext):
        super().__init__(ctx)
        self._thread_update_times: dict[int, float] = {}
        self._registered_views: list = []
        self._transcript_server = None
        self._transcripts_dir: Path | None = None

    async def on_enable(self):
        """Initialize database and register persistent views."""
        if self.db:
            await self.db.initialize(TICKETS_SCHEMA)

        await self._run_migrations()
        await self._cleanup_orphaned_reservations()

        # Validate framework-provided config
        is_valid, errors = validate_config(self.config)
        if not is_valid:
            self.log.error(f"Tickets config validation failed: {errors}")
            for error in errors:
                self.log.error(f"  - {error}")

        # Extract settings from config
        settings = self.config.get("settings", {})
        self.ticket_panel_channel_id = settings.get("ticket_panel_channel_id", 0)
        self.log_channel_id = settings.get("log_channel_id", 0)
        self.staff_role_ids = settings.get("staff_role_ids", [])

        anti_archive = settings.get("anti_archive", {})
        self.anti_archive_enabled = anti_archive.get("enabled", True)
        self.anti_archive_interval = anti_archive.get("check_interval_minutes", 30)

        if self.staff_role_ids == [0]:
            self.log.warning("staff_role_ids contains placeholder value [0] - replace with actual role IDs")

        # Prepare transcripts directory (used by web server and views)
        web_config = settings.get("transcript", {}).get("web", {})
        if web_config.get("enabled", False):
            self._transcripts_dir = Path(self.data_dir) / "transcripts"
            self._transcripts_dir.mkdir(parents=True, exist_ok=True)

        # Configure views module with DB and config references
        configure_views(self.db, self.config, transcripts_dir=self._transcripts_dir)

        self.log.info("Registering persistent views for Tickets")
        panel_view = TicketPanelView()
        panel_view_legacy = TicketPanelView(legacy=True)
        control_view = TicketControlView()
        control_view_legacy = TicketControlView(legacy=True)
        legacy_reminder_view = LegacyReminderView()
        self.bot.add_view(panel_view_legacy)
        self.bot.add_view(panel_view)
        self.bot.add_view(control_view_legacy)
        self.bot.add_view(control_view)
        self.bot.add_view(legacy_reminder_view)
        self._registered_views = [panel_view_legacy, panel_view, control_view_legacy, control_view, legacy_reminder_view]
        self.bot.add_dynamic_items(
            ReminderStopButton, ReminderSnooze1hButton,
            ReminderSnooze6hButton, ReminderSnooze1dButton
        )

        # Transcript web server
        if web_config.get("enabled", False):
            from .web import TranscriptServer

            port = web_config.get("port", 5454)
            base_url = web_config.get("base_url", f"http://localhost:{port}")
            bind_host = web_config.get("bind_host", "127.0.0.1")

            self._transcript_server = TranscriptServer(port, base_url, self._transcripts_dir, self.log, host=bind_host)
            await self._transcript_server.start()

        if self.anti_archive_enabled:
            self.anti_archive_task.change_interval(minutes=self.anti_archive_interval)
            self.anti_archive_task.start()
            self.register_task("anti_archive", self.anti_archive_task)
            self.log.info(f"Anti-archive task started (interval: {self.anti_archive_interval} minutes)")

        self.check_reminders_task.start()
        self.register_task("check_reminders", self.check_reminders_task)
        self.log.info("Reminder check task started (interval: 1 minute)")

        # Periodically delete orphaned transcript HTML files. A transcript can
        # become orphan if the web save succeeded but the subsequent Discord
        # send failed before we recorded transcript_filename — or if a row was
        # later hard-deleted. Disabled when the web server is off.
        if self._transcripts_dir is not None:
            self.cleanup_transcripts_task.start()
            self.register_task("cleanup_transcripts", self.cleanup_transcripts_task)
            self.log.info("Transcript cleanup task started (interval: 6 hours)")

    async def on_ready(self):
        """Validate panel after bot is connected and channel cache is populated."""
        await self.validate_panel()

    async def _run_migrations(self):
        """Run database migrations using the framework's migration system."""
        await self.db.migrate([
            Migration(
                name="add_last_reminder_message_id",
                sql="ALTER TABLE ticket_reminders ADD COLUMN last_reminder_message_id INTEGER"
            ),
            Migration(
                name="add_transcript_message_id",
                sql="ALTER TABLE tickets ADD COLUMN transcript_message_id INTEGER"
            ),
            Migration(
                name="add_transcript_filename",
                sql="ALTER TABLE tickets ADD COLUMN transcript_filename TEXT"
            ),
        ])

    async def _cleanup_orphaned_reservations(self):
        """Drop ticket reservations older than 5 minutes left over from a crashed creation flow."""
        try:
            cursor = await self.db.execute(
                "DELETE FROM tickets WHERE status = 'reserved' AND created_at < datetime('now', '-5 minutes')"
            )
            if cursor.rowcount:
                self.log.info(f"Cleared {cursor.rowcount} orphaned ticket reservation(s)")
        except Exception as e:
            self.log.error(f"Failed to clean up orphaned ticket reservations: {e}")

    async def on_disable(self):
        """Stop background tasks and remove persistent views."""
        if self._transcript_server:
            await self._transcript_server.stop()
            self._transcript_server = None
        if self.anti_archive_task.is_running():
            self.anti_archive_task.cancel()
        if self.check_reminders_task.is_running():
            self.check_reminders_task.cancel()
        if self.cleanup_transcripts_task.is_running():
            self.cleanup_transcripts_task.cancel()
        for view in self._registered_views:
            view.stop()
        self._registered_views.clear()
        self.bot.remove_dynamic_items(
            ReminderStopButton, ReminderSnooze1hButton,
            ReminderSnooze6hButton, ReminderSnooze1dButton
        )
        self.log.info("Tickets branch unloaded")

    async def validate_panel(self):
        """Validate that panel message exists and is up to date."""
        try:
            current_hash = hash_config(self.config)

            row = await self.db.fetchone(
                "SELECT message_id, channel_id, config_hash FROM panel_messages ORDER BY id DESC LIMIT 1"
            )

            needs_update = False

            if row:
                message_id, channel_id, stored_hash = row

                if current_hash != stored_hash:
                    self.log.info("Config changed, panel needs update")
                    needs_update = True

                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        try:
                            old_message = await channel.fetch_message(message_id)
                            await old_message.delete()
                            self.log.info(f"Deleted old panel message {message_id}")
                        except discord.NotFound:
                            self.log.warning(f"Old panel message {message_id} not found")
                        except discord.HTTPException as e:
                            self.log.error(f"Failed to delete old panel: {e}")

                    await self.db.execute("DELETE FROM panel_messages WHERE message_id = ?", (message_id,))
                else:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        try:
                            await channel.fetch_message(message_id)
                            self.log.info(f"Panel message validated: {message_id}")
                        except discord.NotFound:
                            self.log.warning(f"Panel message {message_id} not found - will recreate")
                            needs_update = True
                            await self.db.execute("DELETE FROM panel_messages WHERE message_id = ?", (message_id,))
            else:
                needs_update = True

            if needs_update:
                await self.create_panel()

        except Exception as e:
            self.log.error(f"Error validating panel: {e}", exc_info=True)

    async def create_panel(self):
        """Create the ticket panel message."""
        try:
            old_panels = await self.db.fetchall(
                "SELECT message_id, channel_id FROM panel_messages"
            )

            for message_id, channel_id in old_panels:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    try:
                        old_message = await channel.fetch_message(message_id)
                        await old_message.delete()
                    except discord.NotFound:
                        pass
                    except discord.HTTPException as e:
                        self.log.error(f"Failed to delete old panel {message_id}: {e}")

            await self.db.execute("DELETE FROM panel_messages")

            channel = self.bot.get_channel(self.ticket_panel_channel_id)
            if not channel:
                self.log.error(f"Ticket panel channel {self.ticket_panel_channel_id} not found")
                return

            panel_config = self.config.get("settings", {}).get("panel", {})
            title = panel_config.get("title", "\U0001f3ab Support Tickets")
            description = panel_config.get("description", "Click a button below to create a ticket.")
            color = panel_config.get("color", 0x5865F2)

            embed = discord.Embed(
                title=title,
                description=description,
                color=color
            )

            categories = self.config.get("settings", {}).get("categories", {})
            enabled_cats = [
                (key, cat) for key, cat in categories.items()
                if cat.get("enabled", True)
            ]

            if enabled_cats:
                category_list = []
                for key, cat in enabled_cats:
                    emoji = cat.get("emoji", "\U0001f3ab")
                    label = cat.get("label", key.replace('_', ' ').title())
                    desc = cat.get("description", "")
                    category_list.append(f"{emoji} **{label}**\n{desc}")

                field_name = panel_config.get("categories_field_name", "Available Categories")

                embed.add_field(
                    name=field_name,
                    value="\n\n".join(category_list),
                    inline=False
                )

            view = TicketPanelView()
            message = await channel.send(embed=embed, view=view)

            current_hash = hash_config(self.config)
            await self.db.execute(
                "INSERT INTO panel_messages (message_id, channel_id, config_hash) VALUES (?, ?, ?)",
                (message.id, channel.id, current_hash)
            )

            self.log.info(f"Created new panel message: {message.id}")

        except Exception as e:
            self.log.error(f"Error creating panel: {e}", exc_info=True)

    def _prune_thread_update_times(self) -> None:
        """Drop debounce entries older than the debounce window.

        Called from the listener itself rather than only from the
        anti-archive loop, which doesn't run when anti_archive is disabled —
        that left the dict growing for the lifetime of the process.
        """
        now = time.time()
        stale = [tid for tid, ts in self._thread_update_times.items() if now - ts > 30]
        for tid in stale:
            del self._thread_update_times[tid]

    @tasks.loop(minutes=30)
    async def anti_archive_task(self):
        """Periodically unarchive open ticket threads that were manually archived."""
        self._prune_thread_update_times()

        try:
            open_tickets = await self.db.fetchall(
                "SELECT thread_id FROM tickets WHERE status = 'open'"
            )

            if not open_tickets:
                return

            unarchived = 0
            for (thread_id,) in open_tickets:
                try:
                    thread = self.bot.get_channel(thread_id)
                    if not thread:
                        for guild in self.bot.guilds:
                            try:
                                thread = await guild.fetch_channel(thread_id)
                                if thread:
                                    break
                            except discord.NotFound:
                                continue
                            except discord.HTTPException:
                                continue

                    if thread and isinstance(thread, discord.Thread):
                        if thread.archived:
                            await thread.edit(archived=False)
                            unarchived += 1
                            await asyncio.sleep(1)

                except discord.HTTPException as e:
                    self.log.error(f"Failed to unarchive thread {thread_id}: {e}")
                except Exception as e:
                    self.log.error(f"Error processing thread {thread_id}: {e}")

            if unarchived > 0:
                self.log.info(f"Anti-archive: unarchived {unarchived} ticket thread(s)")

        except Exception as e:
            self.log.error(f"Error in anti-archive task: {e}", exc_info=True)

    @anti_archive_task.before_loop
    async def before_anti_archive_task(self):
        """Wait until bot is ready before starting task."""
        await self.bot.wait_until_ready()

    @anti_archive_task.error
    async def anti_archive_task_error(self, error):
        """Log errors from the anti-archive task."""
        self.log.error(f"Unhandled error in anti_archive_task: {error}", exc_info=True)

    @tasks.loop(minutes=1)
    async def check_reminders_task(self):
        """Check for due reminders and send notifications."""
        try:
            now = datetime.now(timezone.utc)
            colors = get_embed_colors(self.config)

            due_reminders = await self.db.fetchall(
                """SELECT id, ticket_thread_id, user_id, initial_reminder_at, last_reminded_at, dm_enabled, last_reminder_message_id
                FROM ticket_reminders
                WHERE active = 1
                AND (
                    (initial_reminder_at IS NOT NULL AND initial_reminder_at <= ? AND last_reminded_at IS NULL)
                    OR (last_reminded_at IS NOT NULL AND datetime(last_reminded_at, '+1 day') <= ?)
                    OR (initial_reminder_at IS NULL AND last_reminded_at IS NULL AND created_at <= datetime('now', '-1 day'))
                )""",
                (now.strftime('%Y-%m-%d %H:%M:%S'), now.strftime('%Y-%m-%d %H:%M:%S'))
            )

            if not due_reminders:
                return

            for reminder_id, thread_id, user_id, initial_reminder_at, last_reminded_at, dm_enabled, last_reminder_message_id in due_reminders:
                try:
                    thread = self.bot.get_channel(thread_id)
                    if not thread:
                        for guild in self.bot.guilds:
                            try:
                                thread = await guild.fetch_channel(thread_id)
                                if thread:
                                    break
                            except (discord.NotFound, discord.HTTPException):
                                continue

                    if not thread or not isinstance(thread, discord.Thread):
                        self.log.warning(f"Thread {thread_id} not found, deactivating reminder {reminder_id}")
                        await self.db.execute(
                            "UPDATE ticket_reminders SET active = 0 WHERE id = ?",
                            (reminder_id,)
                        )
                        continue

                    user = self.bot.get_user(user_id)
                    if not user:
                        try:
                            user = await self.bot.fetch_user(user_id)
                        except (discord.NotFound, discord.HTTPException):
                            self.log.warning(f"User {user_id} not found for reminder {reminder_id}")
                            continue

                    if last_reminder_message_id:
                        try:
                            old_message = await thread.fetch_message(last_reminder_message_id)
                            await old_message.delete()
                            self.log.info(f"Deleted old reminder message {last_reminder_message_id} for reminder {reminder_id}")
                        except discord.NotFound:
                            self.log.debug(f"Old reminder message {last_reminder_message_id} not found (already deleted)")
                        except discord.HTTPException as e:
                            self.log.warning(f"Failed to delete old reminder message {last_reminder_message_id}: {e}")

                    is_initial = last_reminded_at is None and initial_reminder_at is not None
                    reminder_type = "Initial" if is_initial else "Daily"

                    view = build_reminder_view(reminder_id)
                    embed = discord.Embed(
                        title=f"\U0001f514 {reminder_type} Reminder",
                        description=f"{user.mention}, this is a reminder to check on this ticket.",
                        color=colors["open"]
                    )
                    embed.add_field(
                        name="Ticket",
                        value=thread.mention,
                        inline=True
                    )
                    embed.set_footer(text="Use the buttons below to stop or snooze this reminder")

                    try:
                        reminder_message = await thread.send(content=user.mention, embed=embed, view=view)
                    except discord.HTTPException as e:
                        # Don't refire every minute during a Discord outage or
                        # a permissions hiccup. Bump last_reminded_at so the
                        # next attempt waits a full day, and move on.
                        self.log.error(f"Failed to send reminder for ticket {thread_id}: {e}")
                        try:
                            await self.db.execute(
                                "UPDATE ticket_reminders SET last_reminded_at = ? WHERE id = ?",
                                (now.strftime('%Y-%m-%d %H:%M:%S'), reminder_id),
                            )
                        except Exception:
                            self.log.exception(f"Failed to mark reminder {reminder_id} after send failure")
                        continue
                    self.log.info(f"Sent {reminder_type.lower()} reminder for ticket {thread_id} to user {user_id}")

                    if dm_enabled:
                        try:
                            dm_embed = discord.Embed(
                                title=f"\U0001f514 Ticket Reminder: {thread.name}",
                                description="This is a reminder to check on your ticket.",
                                color=colors["open"]
                            )
                            dm_embed.add_field(
                                name="Ticket",
                                value=f"[{thread.name}](https://discord.com/channels/{thread.guild.id}/{thread.id})",
                                inline=False
                            )
                            await user.send(embed=dm_embed)
                            self.log.info(f"Sent DM reminder to user {user_id}")
                        except discord.Forbidden:
                            self.log.warning(f"Could not DM user {user_id} - DMs disabled")
                        except discord.HTTPException as e:
                            self.log.error(f"Failed to DM user {user_id}: {e}")

                    await self.db.execute(
                        "UPDATE ticket_reminders SET last_reminded_at = ?, last_reminder_message_id = ? WHERE id = ?",
                        (now.strftime('%Y-%m-%d %H:%M:%S'), reminder_message.id, reminder_id)
                    )

                    await asyncio.sleep(1)

                except Exception as e:
                    self.log.error(f"Error processing reminder {reminder_id}: {e}", exc_info=True)

        except Exception as e:
            self.log.error(f"Error in check_reminders_task: {e}", exc_info=True)

    @check_reminders_task.before_loop
    async def before_check_reminders_task(self):
        """Wait until bot is ready before starting task."""
        await self.bot.wait_until_ready()

    @check_reminders_task.error
    async def check_reminders_task_error(self, error):
        """Log errors from the check reminders task."""
        self.log.error(f"Unhandled error in check_reminders_task: {error}", exc_info=True)

    @tasks.loop(hours=6)
    async def cleanup_transcripts_task(self):
        """Delete on-disk transcripts that aren't referenced by any ticket row.

        Orphans arise when the web-save succeeded but recording the filename
        in the tickets row failed, or when a tickets row was later deleted.
        Files newer than the retention window are left alone so an in-flight
        transcript send isn't yanked out from under itself.
        """
        if self._transcripts_dir is None or not self._transcripts_dir.exists():
            return
        retention_hours = self.setting(
            "transcript", "web", "retention_hours", default=24
        )
        retention_seconds = max(1, int(retention_hours)) * 3600
        try:
            referenced_rows = await self.db.fetchall(
                "SELECT transcript_filename FROM tickets WHERE transcript_filename IS NOT NULL"
            )
            referenced = {row[0] for row in referenced_rows if row[0]}
        except Exception as e:
            self.log.error(f"Failed to read transcript filenames for cleanup: {e}")
            return

        now = time.time()
        removed = 0
        for path in self._transcripts_dir.glob("*.html"):
            if path.name in referenced:
                continue
            try:
                if now - path.stat().st_mtime < retention_seconds:
                    continue
                path.unlink()
                removed += 1
            except OSError as e:
                self.log.warning(f"Failed to remove orphan transcript {path.name}: {e}")
        if removed:
            self.log.info(f"Cleaned up {removed} orphan transcript file(s)")

    @cleanup_transcripts_task.before_loop
    async def before_cleanup_transcripts_task(self):
        await self.bot.wait_until_ready()

    @cleanup_transcripts_task.error
    async def cleanup_transcripts_task_error(self, error):
        self.log.error(f"Unhandled error in cleanup_transcripts_task: {error}", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_thread_update(self, payload):
        """Raw thread update event to catch when closed tickets are unarchived."""
        if 'thread_metadata' not in payload.data:
            return

        # Debounce: skip if the same thread was processed within 5 seconds
        self._prune_thread_update_times()
        now = time.time()
        last_update = self._thread_update_times.get(payload.thread_id, 0)
        if now - last_update < 5:
            return
        self._thread_update_times[payload.thread_id] = now

        metadata = payload.data['thread_metadata']
        archived = metadata.get('archived', False)
        locked = metadata.get('locked', False)

        ticket = await self.db.fetchone(
            "SELECT status FROM tickets WHERE thread_id = ?",
            (payload.thread_id,)
        )

        if not ticket:
            return

        status = ticket[0]

        if status == 'closed' and (not archived or not locked):
            ticket_data = await self.db.fetchone(
                "SELECT user_id, category FROM tickets WHERE thread_id = ?",
                (payload.thread_id,)
            )
            if not ticket_data:
                return
            creator_id, category = ticket_data

            # Resolve the thread
            thread = self.bot.get_channel(payload.thread_id)
            if not thread:
                for guild in self.bot.guilds:
                    try:
                        thread = await guild.fetch_channel(payload.thread_id)
                        if thread:
                            break
                    except (discord.NotFound, discord.HTTPException):
                        continue

            if not thread or not isinstance(thread, discord.Thread):
                return

            # Check if the unarchiver is authorized (admin, global staff, or category staff).
            # Look at more recent entries and constrain to the last few minutes so a busy
            # guild's audit log doesn't push the relevant entry out of our window.
            unarchiver = None
            try:
                guild = thread.guild
                cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
                async for entry in guild.audit_logs(limit=25, action=discord.AuditLogAction.thread_update):
                    if entry.created_at < cutoff:
                        break
                    if entry.target and entry.target.id == payload.thread_id:
                        unarchiver = entry.user
                        break
            except (discord.Forbidden, discord.HTTPException) as e:
                self.log.warning(f"Could not check audit log for thread {payload.thread_id}: {e}")

            # If we found an unarchiver, check authorization
            if unarchiver and not unarchiver.bot:
                is_authorized = False

                # Check if admin
                member = thread.guild.get_member(unarchiver.id)
                if member:
                    if member.guild_permissions.administrator:
                        is_authorized = True

                    # Check global staff roles
                    if not is_authorized and any(role.id in self.staff_role_ids for role in member.roles):
                        is_authorized = True

                    # Check category-specific staff roles
                    if not is_authorized:
                        categories = self.config.get("settings", {}).get("categories", {})
                        category_config = categories.get(category, {})
                        cat_staff_roles = category_config.get("staff_roles", category_config.get("ping_roles", []))
                        if any(role.id in cat_staff_roles for role in member.roles):
                            is_authorized = True

                if not is_authorized:
                    # Unauthorized reopen - re-archive and re-lock
                    self.log.warning(
                        f"Unauthorized reopen of ticket {payload.thread_id} by user {unarchiver.id}, re-archiving"
                    )
                    try:
                        await thread.edit(archived=True, locked=True)
                    except discord.HTTPException as e:
                        self.log.error(f"Failed to re-archive unauthorized thread {payload.thread_id}: {e}")
                    return

            elif unarchiver is None:
                # Audit log lookup failed -- deny the reopen for safety
                self.log.error(
                    f"Could not determine who unarchived closed ticket {payload.thread_id}; "
                    "re-archiving (likely missing Audit Log permission)"
                )
                try:
                    await thread.edit(archived=True, locked=True)
                except discord.HTTPException as e:
                    self.log.error(f"Failed to re-archive thread {payload.thread_id}: {e}")
                return

            # Authorized reopen - update DB and notify
            colors = get_embed_colors(self.config)

            await self.db.execute(
                "UPDATE tickets SET status = 'open', reopened_by = NULL, closed_at = NULL WHERE thread_id = ?",
                (payload.thread_id,)
            )

            try:
                await thread.edit(archived=False, locked=False)

                reopen_embed = discord.Embed(
                    title="\U0001f513 Ticket Reopened",
                    description="This ticket has been reopened.",
                    color=colors["open"]
                )
                await thread.send(embed=reopen_embed, view=TicketControlView())

                if self.log_channel_id:
                    log_channel = thread.guild.get_channel(self.log_channel_id)
                    if log_channel:
                        log_embed = format_log_embed(
                            "reopened",
                            {
                                "category": category,
                                "thread_id": thread.id,
                                "creator_id": creator_id
                            },
                            colors=colors
                        )
                        try:
                            await log_channel.send(embed=log_embed)
                        except discord.HTTPException as e:
                            self.log.error(f"Failed to log ticket reopen: {e}")

            except discord.HTTPException as e:
                self.log.error(f"Failed to reopen ticket {payload.thread_id}: {e}")

    @app_commands.command(name="tickets", description="View your open tickets")
    async def list_tickets(self, interaction: discord.Interaction):
        """List user's open tickets."""
        await interaction.response.defer(ephemeral=True)

        try:
            colors = get_embed_colors(self.config)
            tickets = await self.db.fetchall(
                "SELECT thread_id, category, created_at FROM tickets WHERE user_id = ? AND status = 'open' ORDER BY created_at DESC",
                (interaction.user.id,)
            )

            if not tickets:
                await interaction.followup.send(
                    "You don't have any open tickets.",
                    ephemeral=True
                )
                return

            embed = discord.Embed(
                title="\U0001f4cb Your Open Tickets",
                color=colors["open"]
            )

            for thread_id, category, created_at in tickets:
                category_name = category.replace('_', ' ').title()
                embed.add_field(
                    name=f"\U0001f3ab {category_name}",
                    value=f"Thread: <#{thread_id}>\nCreated: {created_at[:16]}",
                    inline=False
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            self.log.error(f"Error listing tickets: {e}", exc_info=True)
            await interaction.followup.send(
                "\u274c An error occurred while fetching your tickets.",
                ephemeral=True
            )

    @app_commands.command(name="reopenticket", description="Reopen a closed ticket (Staff only)")
    async def reopen_ticket(self, interaction: discord.Interaction):
        """Reopen a closed ticket."""
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "\u274c This command can only be used in ticket threads.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        thread = interaction.channel
        colors = get_embed_colors(self.config)

        try:
            ticket = await self.db.fetchone(
                "SELECT user_id, category, status FROM tickets WHERE thread_id = ?",
                (thread.id,)
            )

            if not ticket:
                await interaction.followup.send(
                    "\u274c This is not a valid ticket thread.",
                    ephemeral=True
                )
                return

            creator_id, category, status = ticket

            if not can_manage_ticket_category(interaction, category, self.config):
                await interaction.followup.send(
                    "\u274c You don't have permission to reopen tickets in this category.",
                    ephemeral=True
                )
                return

            if status == 'open':
                await interaction.followup.send(
                    "\u274c This ticket is already open.",
                    ephemeral=True
                )
                return

            await self.db.execute(
                """UPDATE tickets
                SET status = 'open', reopened_by = ?, closed_at = NULL
                WHERE thread_id = ?""",
                (interaction.user.id, thread.id)
            )

            try:
                await thread.edit(archived=False, locked=False)
            except discord.HTTPException as e:
                self.log.error(f"Failed to unarchive/unlock thread: {e}")

            reopen_embed = discord.Embed(
                title="\U0001f513 Ticket Reopened",
                description=f"This ticket has been reopened by {interaction.user.mention}",
                color=colors["open"]
            )

            await thread.send(embed=reopen_embed, view=TicketControlView())

            if self.log_channel_id:
                log_channel = interaction.guild.get_channel(self.log_channel_id)
                if log_channel:
                    log_embed = format_log_embed(
                        "reopened",
                        {
                            "category": category,
                            "thread_id": thread.id,
                            "creator_id": creator_id
                        },
                        user=interaction.user,
                        colors=colors
                    )
                    try:
                        await log_channel.send(embed=log_embed)
                    except discord.HTTPException as e:
                        self.log.error(f"Failed to log ticket reopen: {e}")

            await interaction.followup.send(
                "\u2705 Ticket reopened successfully.",
                ephemeral=True
            )

        except Exception as e:
            self.log.error(f"Error reopening ticket: {e}", exc_info=True)
            await interaction.followup.send(
                "\u274c An error occurred while reopening the ticket.",
                ephemeral=True
            )

    @app_commands.command(name="closeticket", description="Close a ticket with optional reason (Staff only)")
    @app_commands.describe(reason="Reason for closing the ticket (optional)")
    async def close_ticket_command(self, interaction: discord.Interaction, reason: str = None):
        """Close a ticket via slash command (works in any ticket thread)."""
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "\u274c This command can only be used in ticket threads.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        thread = interaction.channel
        colors = get_embed_colors(self.config)

        try:
            ticket = await self.db.fetchone(
                "SELECT user_id, category, status, ticket_number, created_at FROM tickets WHERE thread_id = ?",
                (thread.id,)
            )

            if not ticket:
                await interaction.followup.send(
                    "\u274c This is not a valid ticket thread.",
                    ephemeral=True
                )
                return

            creator_id, category, status, ticket_number, created_at = ticket

            if not can_manage_ticket_category(interaction, category, self.config):
                await interaction.followup.send(
                    "\u274c You don't have permission to close tickets in this category.",
                    ephemeral=True
                )
                return

            if status == 'closed':
                await interaction.followup.send(
                    "\u274c This ticket is already closed.",
                    ephemeral=True
                )
                return

            close_embed = discord.Embed(
                title="\U0001f512 Ticket Closed",
                description=f"This ticket has been closed by {interaction.user.mention}",
                color=colors["closed"]
            )

            if reason:
                close_embed.add_field(name="Reason", value=reason, inline=False)

            try:
                await thread.send(embed=close_embed)
                await asyncio.sleep(0.5)
            except discord.HTTPException as e:
                self.log.error(f"Failed to send close message: {e}")

            # Generate transcript BEFORE archiving (must fetch history while thread is open)
            transcript_buf = None
            if self.setting("transcript", "enabled", default=False):
                try:
                    from .transcript import generate_transcript
                    transcript_buf = await generate_transcript(thread, {
                        "category": category,
                        "ticket_number": ticket_number,
                        "creator_id": creator_id,
                        "created_at": created_at,
                        "closed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                        "closed_by": interaction.user.id,
                        "close_reason": reason,
                    })
                except Exception as e:
                    self.log.error(f"Failed to generate transcript: {e}", exc_info=True)

            # Atomically claim the close. The status='open' predicate prevents
            # two concurrent /closeticket invocations from both "succeeding";
            # only the writer that flipped status='open' -> 'closed' continues.
            async with self.db.transaction() as conn:
                cursor = await conn.execute(
                    """UPDATE tickets
                    SET status = 'closed', closed_by = ?, close_reason = ?, closed_at = datetime('now')
                    WHERE thread_id = ? AND status = 'open'""",
                    (interaction.user.id, reason, thread.id)
                )
                claim_rowcount = cursor.rowcount
                if claim_rowcount:
                    await conn.execute(
                        "UPDATE ticket_reminders SET active = 0 WHERE ticket_thread_id = ? AND active = 1",
                        (thread.id,)
                    )

            if not claim_rowcount:
                await interaction.followup.send(
                    "\u274c This ticket is already closed.",
                    ephemeral=True
                )
                return

            self.log.info(f"Closed ticket {thread.id} and cancelled reminders")

            try:
                await thread.edit(archived=True, locked=True)
            except discord.HTTPException as e:
                self.log.error(f"Failed to close thread: {e}")
                # Revert ONLY if our row is still in the state we just wrote \u2014
                # scoping by closed_by + status='closed' prevents reopening a
                # ticket someone else legitimately closed in the meantime.
                await self.db.execute(
                    """UPDATE tickets
                    SET status = 'open', closed_by = NULL, close_reason = NULL, closed_at = NULL
                    WHERE thread_id = ? AND status = 'closed' AND closed_by = ?""",
                    (thread.id, interaction.user.id)
                )
                await interaction.followup.send(
                    "\u274c Failed to close the ticket thread. Please check bot permissions and try again.",
                    ephemeral=True
                )
                return

            # Send transcript (after archive, outside thread)
            if transcript_buf:
                try:
                    from .transcript import send_transcript
                    await send_transcript(
                        transcript_buf, thread,
                        {"category": category, "ticket_number": ticket_number, "creator_id": creator_id},
                        self.config, self.db, self.bot,
                        transcripts_dir=self._transcripts_dir,
                    )
                except Exception as e:
                    self.log.error(f"Failed to send transcript: {e}", exc_info=True)

            if self.log_channel_id:
                log_channel = interaction.guild.get_channel(self.log_channel_id)
                if log_channel:
                    log_embed = format_log_embed(
                        "closed",
                        {
                            "category": category,
                            "thread_id": thread.id,
                            "creator_id": creator_id
                        },
                        user=interaction.user,
                        reason=reason,
                        colors=colors
                    )
                    try:
                        await log_channel.send(embed=log_embed)
                    except discord.HTTPException as e:
                        self.log.error(f"Failed to log ticket closure: {e}")

            await interaction.followup.send(
                "\u2705 Ticket closed successfully.",
                ephemeral=True
            )

        except Exception as e:
            self.log.error(f"Error closing ticket: {e}", exc_info=True)
            await interaction.followup.send(
                "\u274c An error occurred while closing the ticket.",
                ephemeral=True
            )

    async def category_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete categories from config."""
        categories = self.config.get("settings", {}).get("categories", {})

        choices = [
            app_commands.Choice(
                name=cat_config.get("label", key.replace('_', ' ').title()),
                value=key
            )
            for key, cat_config in categories.items()
            if cat_config.get("enabled", True)
        ]

        if current:
            choices = [
                choice for choice in choices
                if current.lower() in choice.name.lower() or current.lower() in choice.value.lower()
            ]

        return choices[:25]

    @app_commands.command(name="addticket", description="Manually add a thread to the tickets database (Staff only)")
    @app_commands.describe(
        category="Category for this ticket",
        user="User who created the ticket (optional, defaults to thread owner)"
    )
    @app_commands.autocomplete(category=category_autocomplete)
    async def add_ticket(self, interaction: discord.Interaction, category: str, user: discord.User = None):
        """Manually add a thread to the tickets database."""
        if not is_staff(interaction, self.staff_role_ids):
            await interaction.response.send_message(
                "\u274c You don't have permission to use this command.",
                ephemeral=True
            )
            return

        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "\u274c This command can only be used in ticket threads.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        thread = interaction.channel

        try:
            categories = self.config.get("settings", {}).get("categories", {})
            if category not in categories:
                await interaction.followup.send(
                    f"\u274c Invalid category: '{category}'. Please choose from the autocomplete list.",
                    ephemeral=True
                )
                return

            category_config = categories[category]
            if not category_config.get("enabled", True):
                await interaction.followup.send(
                    f"\u274c Category '{category}' is currently disabled.",
                    ephemeral=True
                )
                return

            existing = await self.db.fetchone(
                "SELECT id FROM tickets WHERE thread_id = ?",
                (thread.id,)
            )

            if existing:
                await interaction.followup.send(
                    "\u274c This ticket is already in the database.",
                    ephemeral=True
                )
                return

            if user:
                user_id = user.id
            elif thread.owner:
                user_id = thread.owner.id
            elif thread.owner_id:
                user_id = thread.owner_id
            else:
                await interaction.followup.send(
                    "\u274c Could not determine ticket owner. Please specify a user.",
                    ephemeral=True
                )
                return

            status = "closed" if (thread.archived and thread.locked) else "open"

            ticket_number = None
            naming_pattern = category_config.get("naming_pattern", "")
            if "{number}" in naming_pattern:
                # Use transaction to combine number generation + insert atomically
                async with self.db.transaction() as conn:
                    ticket_number = await get_next_ticket_number(category, conn)
                    await conn.execute(
                        """INSERT INTO tickets
                        (thread_id, user_id, category, ticket_number, status, created_at)
                        VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                        (thread.id, user_id, category, ticket_number, status)
                    )
            else:
                await self.db.execute(
                    """INSERT INTO tickets
                    (thread_id, user_id, category, ticket_number, status, created_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                    (thread.id, user_id, category, ticket_number, status)
                )

            category_name = category.replace('_', ' ').title()
            ticket_identifier = f"#{ticket_number}" if ticket_number else f"ID:{user_id}"
            user_mention = user.mention if user else f"<@{user_id}>"

            await interaction.followup.send(
                f"\u2705 Ticket added to database!\n"
                f"\u2022 Category: {category_name}\n"
                f"\u2022 Ticket: {ticket_identifier}\n"
                f"\u2022 User: {user_mention}\n"
                f"\u2022 Status: {status}\n"
                f"\u2022 Thread: {thread.mention}",
                ephemeral=True
            )

            self.log.info(f"Manually added ticket: {thread.id} ({category}) by staff {interaction.user.id}")

        except Exception as e:
            self.log.error(f"Error adding ticket manually: {e}", exc_info=True)
            await interaction.followup.send(
                "\u274c An error occurred while adding the ticket to the database.",
                ephemeral=True
            )

    @app_commands.command(name="ticketstats", description="View ticket statistics (Staff only)")
    async def ticket_stats(self, interaction: discord.Interaction):
        """Show ticket statistics."""
        if not is_staff(interaction, self.staff_role_ids):
            await interaction.response.send_message(
                "\u274c You don't have permission to view ticket statistics.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            colors = get_embed_colors(self.config)

            # Exclude 'reserved' rows — they're transient placeholders from
            # in-flight ticket creation, not real tickets.
            total_row = await self.db.fetchone(
                "SELECT COUNT(*) FROM tickets WHERE status IN ('open', 'closed')"
            )
            total = total_row[0]

            status_rows = await self.db.fetchall(
                "SELECT status, COUNT(*) FROM tickets WHERE status IN ('open', 'closed') GROUP BY status"
            )
            status_counts = {row[0]: row[1] for row in status_rows}

            category_rows = await self.db.fetchall(
                "SELECT category, COUNT(*) FROM tickets WHERE status IN ('open', 'closed') GROUP BY category ORDER BY COUNT(*) DESC LIMIT 5"
            )

            avg_row = await self.db.fetchone(
                """SELECT AVG(julianday(closed_at) - julianday(created_at))
                FROM tickets WHERE status = 'closed' AND closed_at IS NOT NULL"""
            )
            avg_resolution = avg_row[0] if avg_row[0] else 0

            embed = discord.Embed(
                title="\U0001f4ca Ticket Statistics",
                color=colors["open"]
            )

            embed.add_field(name="Total Tickets", value=f"**{total}**", inline=True)
            embed.add_field(
                name="Open Tickets",
                value=f"**{status_counts.get('open', 0)}**",
                inline=True
            )
            embed.add_field(
                name="Closed Tickets",
                value=f"**{status_counts.get('closed', 0)}**",
                inline=True
            )

            if avg_resolution > 0:
                hours = avg_resolution * 24
                embed.add_field(
                    name="Avg. Resolution Time",
                    value=f"**{hours:.1f}** hours",
                    inline=True
                )

            if category_rows:
                cat_text = "\n".join([
                    f"**{cat.replace('_', ' ').title()}:** {count}"
                    for cat, count in category_rows
                ])
                embed.add_field(
                    name="Top Categories",
                    value=cat_text,
                    inline=False
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            self.log.error(f"Error getting ticket stats: {e}", exc_info=True)
            await interaction.followup.send(
                "\u274c An error occurred while fetching statistics.",
                ephemeral=True
            )

    @app_commands.command(name="remindme", description="Set a reminder for this ticket")
    @app_commands.describe(
        time="When to remind (e.g., 30m, 1h, 2h, 1d) - Optional",
        dm="Also send a DM reminder (true/false) - Optional"
    )
    async def remind_me(self, interaction: discord.Interaction, time: str = None, dm: bool = False):
        """Set a reminder for this ticket."""
        from .helpers import parse_time_string

        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "\u274c This command can only be used in ticket threads.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            thread = interaction.channel

            ticket_row = await self.db.fetchone(
                "SELECT user_id, status FROM tickets WHERE thread_id = ?",
                (thread.id,)
            )

            if not ticket_row:
                await interaction.followup.send(
                    "\u274c This doesn't appear to be a ticket thread.",
                    ephemeral=True
                )
                return

            ticket_creator_id, ticket_status = ticket_row

            if ticket_status != 'open':
                await interaction.followup.send(
                    "\u274c You can only set reminders for open tickets.",
                    ephemeral=True
                )
                return

            existing = await self.db.fetchone(
                "SELECT id FROM ticket_reminders WHERE ticket_thread_id = ? AND user_id = ? AND active = 1",
                (thread.id, interaction.user.id)
            )

            if existing:
                await interaction.followup.send(
                    "\u274c You already have an active reminder for this ticket. Stop it first before creating a new one.",
                    ephemeral=True
                )
                return

            initial_reminder_seconds = None
            initial_reminder_at = None

            if time:
                initial_reminder_seconds = parse_time_string(time)
                if initial_reminder_seconds is None:
                    await interaction.followup.send(
                        "\u274c Invalid time format. Use formats like: `30m`, `1h`, `2h`, `1d`",
                        ephemeral=True
                    )
                    return

                initial_reminder_at = datetime.now(timezone.utc) + timedelta(seconds=initial_reminder_seconds)

            try:
                await self.db.execute(
                    """INSERT INTO ticket_reminders
                    (ticket_thread_id, user_id, initial_reminder_at, last_reminded_at, dm_enabled, active)
                    VALUES (?, ?, ?, ?, ?, 1)""",
                    (
                        thread.id,
                        interaction.user.id,
                        initial_reminder_at.strftime('%Y-%m-%d %H:%M:%S') if initial_reminder_at else None,
                        None,
                        1 if dm else 0
                    )
                )
            except IntegrityError:
                await interaction.followup.send(
                    "\u274c You already have an active reminder for this ticket.",
                    ephemeral=True
                )
                return

            msg_parts = ["\u2705 Reminder set!"]

            if time:
                msg_parts.append(f"**Initial reminder:** In {time}")

            msg_parts.append("**Daily reminders:** Enabled (starts after initial or in 24h)")

            if dm:
                msg_parts.append("**DM notifications:** Enabled")

            msg_parts.append("\n*You'll receive reminder messages in this thread with buttons to stop or snooze.*")

            await interaction.followup.send("\n".join(msg_parts), ephemeral=True)
            self.log.info(f"User {interaction.user.id} set reminder for ticket {thread.id} (time={time}, dm={dm})")

        except Exception as e:
            self.log.error(f"Error creating reminder: {e}", exc_info=True)
            await interaction.followup.send(
                "\u274c An error occurred while creating the reminder.",
                ephemeral=True
            )

    @app_commands.command(name="stopreminder", description="Stop your reminder for this ticket")
    async def stop_reminder(self, interaction: discord.Interaction):
        """Stop a reminder for this ticket."""
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "\u274c This command can only be used in ticket threads.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            thread = interaction.channel

            reminder = await self.db.fetchone(
                """SELECT id FROM ticket_reminders
                WHERE ticket_thread_id = ? AND user_id = ? AND active = 1""",
                (thread.id, interaction.user.id)
            )

            if not reminder:
                await interaction.followup.send(
                    "\u274c You don't have an active reminder for this ticket.",
                    ephemeral=True
                )
                return

            reminder_id = reminder[0]

            await self.db.execute(
                "UPDATE ticket_reminders SET active = 0 WHERE id = ?",
                (reminder_id,)
            )

            await interaction.followup.send(
                "\u2705 Your reminder for this ticket has been stopped.",
                ephemeral=True
            )
            self.log.info(f"User {interaction.user.id} stopped reminder {reminder_id} for ticket {thread.id}")

        except Exception as e:
            self.log.error(f"Error stopping reminder: {e}", exc_info=True)
            await interaction.followup.send(
                "\u274c An error occurred while stopping the reminder.",
                ephemeral=True
            )
