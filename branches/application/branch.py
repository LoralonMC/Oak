import discord
from discord.ext import commands
from discord.ui import View, Modal, TextInput, button
import aiosqlite
import json
import mysql.connector
import asyncio
import logging
from pathlib import Path
import yaml
from database import init_cog_database
from utils import check_application_answer_quality, sanitize_text
from config import GUILD_ID

logger = logging.getLogger(__name__)

# Database schema for applications
APPLICATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    channel_id INTEGER UNIQUE,
    app_index INTEGER,
    answers TEXT,
    status TEXT,
    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# Global config - loaded once and shared across all instances
_APPLICATION_CONFIG = None

# Database path - set by Application branch when loaded
_DB_PATH = None

def get_db_path():
    """Get the database path for this branch."""
    if _DB_PATH is None:
        # Fallback to branch folder
        return str(Path(__file__).parent / "data.db")
    return _DB_PATH

def get_application_config():
    """Load application config from config.yml."""
    global _APPLICATION_CONFIG
    if _APPLICATION_CONFIG is None:
        config_path = Path(__file__).parent / "config.yml"
        try:
            with open(config_path, "r") as f:
                _APPLICATION_CONFIG = yaml.safe_load(f) or {}
            logger.info("Loaded application config")
        except Exception as e:
            logger.error(f"Failed to load application config: {e}")
            _APPLICATION_CONFIG = {}
    return _APPLICATION_CONFIG

def is_application_reviewer():
    """Check if user has application reviewer permissions."""
    async def predicate(ctx):
        config = get_application_config()
        reviewer_role_ids = config.get("settings", {}).get("reviewer_role_ids", [])
        user_role_ids = [role.id for role in ctx.author.roles]
        return any(role_id in reviewer_role_ids for role_id in user_role_ids)
    return commands.check(predicate)

def get_application_questions():
    """Get application questions from config."""
    config = get_application_config()
    questions = config.get("settings", {}).get("questions", [])

    # If no questions in config, use these defaults
    if not questions:
        questions = [
            {"label": "What is your username?", "max_length": 50},
            {"label": "What is your age?", "max_length": 20},
            {"label": "How long have you been part of the community?", "max_length": 100},
            {"label": "Why do you want to join the staff team?", "max_length": 1000},
        ]

    return questions

def paginate_application_embed(applicant, answers):
    """
    Returns a list of embeds, paginated by Discord's field and character limits.
    """
    max_fields = 25
    max_total_length = 6000
    max_field_len = 1024
    embeds = []
    questions = get_application_questions()
    total_questions = len(answers)
    fields = []
    char_count = 0

    def make_embed(fields, page_num, total_pages):
        embed = discord.Embed(
            title=f"Application from {applicant.mention if applicant else f'<@{applicant.id}>'}",
            color=discord.Color.blurple()
        )
        if applicant:
            embed.set_author(name=str(applicant), icon_url=applicant.display_avatar.url)
            embed.set_thumbnail(url=applicant.display_avatar.url)
        for label, value in fields:
            embed.add_field(name=label, value=value, inline=False)
        if total_pages > 1:
            embed.set_footer(text=f"Page {page_num} of {total_pages}")
        return embed

    # First, gather fields for each embed, making sure to respect both field and char limits
    all_embeds = []
    i = 0
    while i < total_questions:
        fields = []
        char_count = 0
        fields_in_this_embed = 0
        while i < total_questions and fields_in_this_embed < max_fields and char_count < max_total_length:
            label = questions[i]['label']
            answer = answers[i]
            value = answer[:max_field_len - 3] + "..." if len(answer) > max_field_len else (answer or "*No response*")
            # Add size of this field (label + value + field overhead)
            added_chars = len(label) + len(value) + 50  # 50 is a fudge factor for formatting
            if fields_in_this_embed >= max_fields or char_count + added_chars > max_total_length:
                break
            fields.append((label, value))
            char_count += added_chars
            fields_in_this_embed += 1
            i += 1
        all_embeds.append(fields)

    total_pages = len(all_embeds)
    return [make_embed(fields, idx+1, total_pages) for idx, fields in enumerate(all_embeds)]



def is_staff(member):
    """Check if member has application reviewer permissions."""
    config = get_application_config()
    reviewer_role_ids = config.get("settings", {}).get("reviewer_role_ids", [])
    return any(role.id in reviewer_role_ids for role in getattr(member, "roles", []))

def get_reviewer_role_ids():
    """Get reviewer role IDs from config."""
    config = get_application_config()
    return tuple(config.get("settings", {}).get("reviewer_role_ids", []))

async def fetch_playtime_embed(mc_name):
    """Fetch playtime data from Plan DB/MySQL."""
    config = get_application_config()
    mysql_config = config.get("settings", {}).get("mysql", {})

    if not mysql_config.get("enabled", False):
        return discord.Embed(
            title="Playtime Data",
            description="MySQL/Plan is not enabled in config.",
            color=discord.Color.red()
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
        cursor.execute("SELECT uuid FROM plan_users WHERE name = %s", (mc_name,))
        row = cursor.fetchone()

        if not row:
            return discord.Embed(
                title="Playtime Data",
                description=f"No player found with username **{mc_name}**.",
                color=discord.Color.orange()
            )

        uuid = row['uuid']
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
                hours = seconds // 3600
                minutes = (seconds % 3600) // 60
                return f"{int(hours)}h {int(minutes)}m"

            embed = discord.Embed(
                title="Playtime Data",
                description=f"Playtime for **{mc_name}**",
                color=discord.Color.blue()
            )
            embed.add_field(name="Last 30 days", value=fmt(stats['last_30_days']))
            embed.add_field(name="Last 7 days", value=fmt(stats['last_7_days']))
        else:
            embed = discord.Embed(
                title="Playtime Data",
                description=f"No playtime stats found for **{mc_name}**.",
                color=discord.Color.orange()
            )
    except mysql.connector.Error as e:
        logger.error(f"MySQL error fetching playtime for {mc_name}: {e}")
        embed = discord.Embed(
            title="Playtime Data",
            description=f"Database error: {str(e)}",
            color=discord.Color.red()
        )
    except Exception as e:
        logger.error(f"Unexpected error fetching playtime for {mc_name}: {e}")
        embed = discord.Embed(
            title="Playtime Data",
            description=f"Error: {str(e)}",
            color=discord.Color.red()
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

class ApplicationModal(Modal):
    def __init__(self, step: int, answers: list):
        config = get_application_config()
        position_name = config.get("settings", {}).get("application", {}).get("position_name", "Staff")
        super().__init__(title=f"📝 {position_name} Application – Page {step + 1}")
        self.step = step
        self.answers = answers
        self.all_questions = get_application_questions()
        self.questions = self.all_questions[step * 5: (step + 1) * 5]
        for i, q in enumerate(self.questions):
            self.add_item(TextInput(
                label=q["label"][:45],
                style=discord.TextStyle.paragraph,
                required=True,
                max_length=q.get("max_length", 1000),
                placeholder=q.get("placeholder", "")[:100],  # Discord limit is 100 chars
                custom_id=f"q{i}"
            ))

    async def on_submit(self, interaction: discord.Interaction):
        # Validate all answers before proceeding
        validation_errors = []

        for i, item in enumerate(self.children):
            question_idx = self.step * 5 + i
            if question_idx < len(self.all_questions):
                question = self.all_questions[question_idx]['label']
                answer = sanitize_text(item.value, max_length=self.all_questions[question_idx].get('max_length', 1000))

                is_valid, error_msg = check_application_answer_quality(question, answer)
                if not is_valid:
                    validation_errors.append(f"**{question}**\n{error_msg}")

        # If there are validation errors, show them to the user
        if validation_errors:
            error_embed = discord.Embed(
                title="❌ Please Review Your Answers",
                description="Some of your answers need improvement:\n\n" + "\n\n".join(validation_errors[:3]),  # Show first 3 errors
                color=discord.Color.red()
            )
            error_embed.set_footer(text="Please click the button again and provide better answers.")
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
            return

        # Sanitize and save answers
        sanitized_answers = [
            sanitize_text(item.value, max_length=self.questions[i].get('max_length', 1000))
            for i, item in enumerate(self.children)
        ]
        self.answers.extend(sanitized_answers)
        remaining = len(self.all_questions) - len(self.answers)

        try:
            await interaction.channel.purge(
                check=lambda m: (
                    m.author == interaction.client.user and m.embeds and
                    m.embeds[0].title and "questions submitted" in m.embeds[0].title.lower()
                ),
                limit=10
            )
        except discord.HTTPException as e:
            logger.warning(f"Failed to purge messages: {e}")

        if remaining > 0:
            # More questions to answer
            await interaction.response.defer()
            await interaction.channel.send(
                embed=discord.Embed(
                    title=f"✅ First {len(self.answers)} questions submitted!",
                    description=f"Only {remaining} more to go. Please continue your application below:",
                    color=discord.Color.blue()
                ),
                view=ContinueView(step=self.step + 1, answers=self.answers)
            )
        else:
            # All questions answered - RESPOND TO INTERACTION FIRST
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Application Complete!",
                    description="Your application has been submitted and is being reviewed.",
                    color=discord.Color.green()
                ),
                ephemeral=True
            )

            # THEN CONTINUE WITH CHANNEL OPERATIONS
            # Clean up all bot messages (welcome, continuation, questions submitted)
            try:
                await interaction.channel.purge(
                    check=lambda m: (
                        m.author == interaction.client.user
                        and m.embeds
                        and m.embeds[0].title
                        and ("questions submitted" in m.embeds[0].title.lower()
                             or "continue application" in m.embeds[0].title.lower()
                             or "welcome to the application process" in m.embeds[0].title.lower())
                    ),
                    limit=20
                )
            except discord.HTTPException as e:
                logger.warning(f"Failed to purge application messages: {e}")

            applicant = interaction.guild.get_member(interaction.user.id) or interaction.user

            # Load config for reviewer roles and other settings
            config = get_application_config()

            embed = discord.Embed(
                title="🎉 Application Submitted",
                description=(
                    "Thank you for completing your application!\n\n"
                    "Our staff team will review your responses and reach out here if we need more information. "
                    "You will be notified when a decision is made."
                ),
                color=discord.Color.green()
            )

            # Create staff review thread if this is a TextChannel
            if isinstance(interaction.channel, discord.TextChannel):
                try:
                    thread = await interaction.channel.create_thread(
                        name=f"Staff Review ({interaction.user.display_name})",
                        auto_archive_duration=10080,  # 7 days
                        reason="Staff review for application"
                    )
                    reviewer_role_ids = config.get("settings", {}).get("reviewer_role_ids", [])
                    staff_mentions = " ".join(f"<@&{rid}>" for rid in reviewer_role_ids)
                    await thread.send(
                        content=staff_mentions,
                        embed=discord.Embed(
                            title="Staff Review Thread",
                            description="Discuss this application here.",
                            color=discord.Color.blurple()
                        )
                    )
                except discord.HTTPException as e:
                    logger.error(f"Failed to create review thread: {e}")

            embed.set_author(name=str(applicant), icon_url=applicant.display_avatar.url)
            embed.set_thumbnail(url=applicant.display_avatar.url)

            try:
                await interaction.channel.send(
                    embed=embed,
                    view=PostSubmissionView()
                )
            except discord.HTTPException as e:
                logger.error(f"Failed to send submission message: {e}")

            # Notify admin chat
            config = get_application_config()
            admin_chat_id = config.get("settings", {}).get("admin_chat_id", 0)
            admin_chat = interaction.guild.get_channel(admin_chat_id) if admin_chat_id else None
            if admin_chat:
                try:
                    notif = discord.Embed(
                        title="🆕 New Staff Application",
                        description=f"Applicant: {applicant.mention}\nChannel: [Jump to application]({interaction.channel.jump_url})",
                        color=discord.Color.blurple()
                    )
                    notif.set_thumbnail(url=applicant.display_avatar.url)
                    await admin_chat.send(embed=notif)
                except discord.HTTPException as e:
                    logger.error(f"Failed to send admin notification: {e}")

            # Check Discord linkage
            required_link_role_id = config.get("settings", {}).get("required_link_role_id", 0)
            if required_link_role_id:
                member = interaction.guild.get_member(interaction.user.id)
                if member and required_link_role_id not in [role.id for role in member.roles]:
                    try:
                        await interaction.channel.send(
                            embed=discord.Embed(
                                title="Link your Minecraft Account",
                                description=":link: To ensure the application process goes smoothly, please link your Minecraft account to Discord using `/link` in-game and sending the code to the bot.",
                                color=discord.Color.orange()
                            )
                        )
                    except discord.HTTPException as e:
                        logger.error(f"Failed to send link reminder: {e}")

            # Try to DM the user
            try:
                await interaction.user.send(embed=discord.Embed(
                    title="Application Submitted!",
                    description="Thank you for applying. We'll be in touch soon! 👀",
                    color=discord.Color.green()
                ))
            except discord.Forbidden:
                try:
                    await interaction.channel.send(embed=discord.Embed(
                        description=":warning: I couldn't DM the applicant. Please ensure DMs are enabled.",
                        color=discord.Color.orange()
                    ))
                except discord.HTTPException as e:
                    logger.error(f"Failed to send DM warning: {e}")

            # Update database
            try:
                async with aiosqlite.connect(get_db_path()) as db:
                    await db.execute(
                        "UPDATE applications SET answers = ?, status = 'pending' WHERE channel_id = ?",
                        (json.dumps(self.answers), interaction.channel.id)
                    )
                    await db.commit()
            except Exception as e:
                logger.error(f"Failed to update application in database: {e}")

class ContinueView(View):
    def __init__(self, step: int = 0, answers: list = None):
        super().__init__(timeout=None)
        self.step = step
        self.answers = answers if answers is not None else []

    @button(label="Continue", style=discord.ButtonStyle.green, custom_id="continue_application")
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ApplicationModal(step=self.step, answers=self.answers))

class PostSubmissionView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @button(label="Read", style=discord.ButtonStyle.gray, custom_id="admin_read")
    async def read(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            async with aiosqlite.connect(get_db_path()) as db:
                async with db.execute("SELECT user_id, answers FROM applications WHERE channel_id = ?", (interaction.channel.id,)) as cursor:
                    row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message("No application data found.", ephemeral=True)
                return

            applicant_id, answers_json = row
            answers = json.loads(answers_json)
            applicant = interaction.guild.get_member(applicant_id) or interaction.user

            embeds = paginate_application_embed(applicant, answers)

            try:
                await interaction.response.send_message(embed=embeds[0], ephemeral=True)
            except discord.HTTPException as exc:
                logger.error(f"Failed to send first embed: {exc}")
                await interaction.response.send_message("Failed to send application data.", ephemeral=True)
                return

            # If multiple embeds (pagination), send as additional followups
            for embed in embeds[1:]:
                await asyncio.sleep(1)
                try:
                    await interaction.followup.send(embed=embed, ephemeral=True)
                except discord.HTTPException as exc:
                    logger.error(f"Failed to send followup embed: {exc}")
        except Exception as e:
            logger.error(f"Error reading application: {e}")
            try:
                await interaction.response.send_message("An error occurred while reading the application.", ephemeral=True)
            except:
                pass

    @button(label="Manage", style=discord.ButtonStyle.primary, custom_id="admin_manage")
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            async with aiosqlite.connect(get_db_path()) as db:
                async with db.execute(
                    "SELECT user_id FROM applications WHERE channel_id = ?", (interaction.channel.id,)
                ) as cursor:
                    row = await cursor.fetchone()

            if not row:
                await interaction.response.send_message("No application data found.", ephemeral=True)
                return

            applicant_id = row[0]

            if not is_staff(interaction.user):
                await interaction.response.send_message(
                    embed=discord.Embed(
                        description="❌ You don't have permission to manage applications.",
                        color=discord.Color.red()
                    ), ephemeral=True)
                return

            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Manage Application",
                    description="Select an action below.",
                    color=discord.Color.blurple()
                ),
                view=ManageView(),
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in manage button: {e}")
            try:
                await interaction.response.send_message("An error occurred.", ephemeral=True)
            except:
                pass



class ManageView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @button(label="Accept", style=discord.ButtonStyle.success, custom_id="admin_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with aiosqlite.connect(get_db_path()) as db:
            async with db.execute("SELECT user_id FROM applications WHERE channel_id = ?", (interaction.channel.id,)) as cursor:
                row = await cursor.fetchone()
            if not row:
                await interaction.response.send_message("No application data found.", ephemeral=True)
                return
            applicant_id = row[0]

            # Update application status to accepted
            await db.execute(
                "UPDATE applications SET status = 'accepted' WHERE channel_id = ?",
                (interaction.channel.id,)
            )
            await db.commit()

        applicant = interaction.guild.get_member(applicant_id)
        dm_failed = False

        # DM the user
        if applicant:
            try:
                await applicant.send(
                    embed=discord.Embed(
                        title="🎉 Congratulations! You've Been Accepted.",
                        description=(
                            "Your application has been **accepted**!\n\n"
                            "A staff member will reach out to arrange your next steps. Welcome aboard, and thank you for your interest in helping our community!\n\n"
                            "*Please keep an eye on this channel for further instructions.*"
                        ),
                        color=discord.Color.green()
                    )
                )
            except discord.Forbidden:
                dm_failed = True

        # Public message in the ticket
        await interaction.channel.send(
            embed=discord.Embed(
                title="Application Accepted",
                description=f"🎉 <@{applicant_id}>, your application has been accepted!\nA staff member will reach out to arrange your next steps.",
                color=discord.Color.green()
            )
        )
        if dm_failed:
            await interaction.channel.send(
                embed=discord.Embed(
                    description=f":warning: I couldn't DM <@{applicant_id}> about their acceptance (DMs closed).",
                    color=discord.Color.orange()
                )
            )
        else:
            await interaction.channel.send(
                embed=discord.Embed(
                    description=f"✅ <@{applicant_id}> has been notified via DM.",
                    color=discord.Color.green()
                )
            )
        await interaction.response.send_message("Application accepted!", ephemeral=True)

    @button(label="Move to Accepted", style=discord.ButtonStyle.blurple, custom_id="admin_move")
    async def move(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = get_application_config()
        accepted_category_id = config.get("settings", {}).get("accepted_category_id", 0)
        new_cat = discord.utils.get(interaction.guild.categories, id=accepted_category_id)
        await interaction.channel.edit(category=new_cat)
        await interaction.response.send_message("Moved to Accepted category.", ephemeral=True)

    @button(label="Decline", style=discord.ButtonStyle.danger, custom_id="admin_decline")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Get applicant_id for DeclineReasonModal
        async with aiosqlite.connect(get_db_path()) as db:
            async with db.execute("SELECT user_id FROM applications WHERE channel_id = ?", (interaction.channel.id,)) as cursor:
                row = await cursor.fetchone()
        if not row:
            await interaction.response.send_message("No application data found.", ephemeral=True)
            return
        applicant_id = row[0]
        await interaction.response.send_modal(DeclineReasonModal(applicant_id))

    @button(label="Background Check", style=discord.ButtonStyle.secondary, custom_id="admin_bgcheck")
    async def bgcheck(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Get MC name and applicant_id from DB
        async with aiosqlite.connect(get_db_path()) as db:
            async with db.execute("SELECT user_id, answers FROM applications WHERE channel_id = ?", (interaction.channel.id,)) as cursor:
                row = await cursor.fetchone()
        if not row:
            await interaction.response.send_message("No application data found.", ephemeral=True)
            return
        applicant_id, answers_json = row
        answers = json.loads(answers_json)
        mc_name = answers[0] if answers else None
        # Playtime
        playtime_embed = await fetch_playtime_embed(mc_name) if mc_name else None
        # Punishment history
        embed = discord.Embed(
            title=f"Background Check: {mc_name or 'Unknown'}",
            color=discord.Color.gold()
        )
        embed.description = f"**Playtime:** (see below)\n"
        config = get_application_config()
        punishment_forum_id = config.get("settings", {}).get("punishment_forum_channel_id", 0)
        if punishment_forum_id:
            punishment_channel = interaction.guild.get_channel(punishment_forum_id)
            linked_threads = []
            if isinstance(punishment_channel, discord.ForumChannel):
                for thread in punishment_channel.threads:
                    if mc_name and mc_name.lower() in thread.name.lower():
                        linked_threads.append(thread)
            if linked_threads:
                embed.add_field(
                    name="Punishment History Posts",
                    value="\n".join([f"[{t.name}]({t.jump_url})" for t in linked_threads]),
                    inline=False
                )
            else:
                embed.add_field(
                    name="Punishment History Posts",
                    value="No posts found.",
                    inline=False
                )
        else:
            embed.add_field(
                name="Punishment History Posts",
                value="No forum channel set in config.",
                inline=False
            )
        await interaction.response.send_message(
            embeds=[embed, playtime_embed] if playtime_embed else [embed], ephemeral=True
        )

class DeclineReasonModal(Modal):
    def __init__(self, applicant_id: int):
        super().__init__(title="Reason for Denial")
        self.applicant_id = applicant_id
        self.reason = TextInput(label="Why are you declining this application?", style=discord.TextStyle.paragraph)
        self.add_item(self.reason)
    async def on_submit(self, interaction: discord.Interaction):
        # Update application status to denied
        async with aiosqlite.connect(get_db_path()) as db:
            await db.execute(
                "UPDATE applications SET status = 'denied' WHERE channel_id = ?",
                (interaction.channel.id,)
            )
            await db.commit()

        applicant = interaction.guild.get_member(self.applicant_id)
        dm_failed = False

        # DM the user
        if applicant:
            try:
                await applicant.send(
                    embed=discord.Embed(
                        title="Application Update",
                        description=(
                            "We're sorry to inform you that your application has been **denied**.\n\n"
                            f"**Reason:** {self.reason.value}\n\n"
                            "We encourage you to continue contributing to the community and consider reapplying in the future."
                        ),
                        color=discord.Color.red()
                    )
                )
            except discord.Forbidden:
                dm_failed = True

        # Public message in the ticket
        await interaction.channel.send(
            embed=discord.Embed(
                title="Application Denied",
                description=f"❌ Application for <@{self.applicant_id}> was denied.\n\n**Reason:** {self.reason.value}",
                color=discord.Color.red()
            )
        )
        if dm_failed:
            await interaction.channel.send(
                embed=discord.Embed(
                    description=f":warning: I couldn't DM <@{self.applicant_id}> about their denial (DMs closed).",
                    color=discord.Color.orange()
                )
            )
        else:
            await interaction.channel.send(
                embed=discord.Embed(
                    description=f"✅ <@{self.applicant_id}> has been notified via DM.",
                    color=discord.Color.green()
                )
            )
        await interaction.response.send_message("Denied.", ephemeral=True)


class StartCancelView(View):
    def __init__(self):
        super().__init__(timeout=None)
    @button(label="Start Application", style=discord.ButtonStyle.green, custom_id="start_application")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ApplicationModal(step=0, answers=[]))
    @button(label="Cancel", style=discord.ButtonStyle.red, custom_id="cancel_application")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Application Cancelled",
                description="Your application has been cancelled. This channel will now be deleted.",
                color=discord.Color.red()
            ), ephemeral=True)
        await interaction.channel.delete()

class ApplicationButtonView(View):
    def __init__(self):
        super().__init__(timeout=None)
        # Track users currently creating applications to prevent race conditions
        self._creating_users = set()
        # Note: Button label is set in the decorator, can't be dynamically changed without recreating the view

    @button(label="Apply for Staff", style=discord.ButtonStyle.green, custom_id="apply_button")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id

        # Prevent race condition - check if user is already creating an application
        if user_id in self._creating_users:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Application Already in Progress",
                    description="⏳ Your application is already being created. Please wait...",
                    color=discord.Color.orange()
                ),
                ephemeral=True
            )
            return

        # Mark user as creating an application
        self._creating_users.add(user_id)

        try:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Creating Application Channel",
                    description="⏳ Please wait while your application channel is created...",
                    color=discord.Color.blurple()
                ),
                ephemeral=True
            )
            await handle_application_start(interaction)
        finally:
            # Always remove user from creating set
            self._creating_users.discard(user_id)

async def handle_application_start(interaction: discord.Interaction):
    user = interaction.user
    guild = interaction.guild

    try:
        async with aiosqlite.connect(get_db_path()) as db:
            # Check for existing applications first (before creating anything)
            async with db.execute(
                    "SELECT channel_id, status FROM applications WHERE user_id = ? AND status IN ('in_progress', 'pending')",
                    (user.id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    channel_id, status = row
                    existing_channel = guild.get_channel(channel_id)
                    if existing_channel:
                        await interaction.followup.send(
                            embed=discord.Embed(
                                title="You already have an open application!",
                                description=f"Please continue your application here: {existing_channel.mention}\n\nStatus: **{status.title()}**",
                                color=discord.Color.orange()
                            ),
                            ephemeral=True
                        )
                        return
                    else:
                        # Channel was deleted but application still exists - clean it up
                        await db.execute("UPDATE applications SET status = 'cancelled' WHERE channel_id = ?", (channel_id,))
                        await db.commit()
                        logger.info(f"Cleaned up orphaned application (channel {channel_id}) for user {user.id}")

            # Get next application index
            async with db.execute("SELECT MAX(app_index) FROM applications") as cursor:
                max_index = await cursor.fetchone()
                next_index = (max_index[0] or 0) + 1

            # Load config first
            config = get_application_config()
            application_category_id = config.get("settings", {}).get("application_category_id", 0)
            channel_name_prefix = config.get("settings", {}).get("application", {}).get("channel_name_prefix", "application")

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
            reviewer_role_ids = config.get("settings", {}).get("reviewer_role_ids", [])
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
                logger.error(f"Application category {application_category_id} not found")
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="Configuration Error",
                        description="Application system is not properly configured. Please contact an administrator.",
                        color=discord.Color.red()
                    ),
                    ephemeral=True
                )
                return

            channel = await guild.create_text_channel(
                name=f"{channel_name_prefix}-{next_index:02}",
                category=category,
                overwrites=overwrites,
                reason=f"Application created by {user}"
            )

            # Save to database
            await db.execute(
                "INSERT INTO applications (user_id, channel_id, app_index, answers, status, submitted_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (user.id, channel.id, next_index, "[]", "in_progress")
            )
            await db.commit()
            logger.info(f"Created application #{next_index} for {user} (ID: {user.id}) in channel {channel.id}")

        # Try to DM the user
        try:
            await user.send(embed=discord.Embed(
                title="Application Started",
                description=f"Your application channel is {channel.mention}.",
                color=discord.Color.green()
            ))
        except discord.Forbidden:
            await channel.send(embed=discord.Embed(
                description=":warning: Couldn't DM applicant. Please remind them to open DMs.",
                color=discord.Color.orange()
            ))
        except Exception as e:
            logger.error(f"Error sending DM to applicant {user.id}: {e}")

        # Send welcome message in application channel
        await channel.send(
            content=user.mention,
            embed=discord.Embed(
                title="👋 Welcome to the Application Process",
                description=(
                    "Use the buttons below to begin your application or cancel if you changed your mind.\n\n"
                    "**Before you start:**\n"
                    "• Answer all questions honestly and thoroughly\n"
                    "• This will take about 5-10 minutes\n"
                    "• Your progress is saved after each page\n\n"
                    "Good luck! 🍀"
                ),
                color=discord.Color.blurple()
            ),
            view=StartCancelView()
        )

        # Confirm to user
        await interaction.followup.send(
            embed=discord.Embed(
                title="Application Channel Created!",
                description=f"Your application channel is ready: {channel.mention}\n\nHead there to start your application.",
                color=discord.Color.green()
            ),
            ephemeral=True
        )

    except discord.HTTPException as e:
        logger.error(f"Discord API error creating application for {user.id}: {e}")
        try:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="Error Creating Application",
                    description="Failed to create your application channel. Please try again later or contact an administrator.",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )
        except:
            pass
    except Exception as e:
        logger.error(f"Unexpected error creating application for {user.id}: {e}", exc_info=True)
        try:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="Error",
                    description="An unexpected error occurred. Please contact an administrator.",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )
        except:
            pass


class ApplicationCog(commands.Cog):
    def __init__(self, bot):
        global _DB_PATH
        self.bot = bot
        self._application_button_view = ApplicationButtonView()

        # Set database path (in this branch's folder)
        self.db_path = str(Path(__file__).parent / "data.db")
        _DB_PATH = self.db_path  # Set module-level path for standalone functions

        # Load config
        self.config = get_application_config()
        settings = self.config.get("settings", {})

        # Cache frequently used settings
        self.application_channel_id = settings.get("application_channel_id", 0)
        self.application_category_id = settings.get("application_category_id", 0)
        self.accepted_category_id = settings.get("accepted_category_id", 0)
        self.admin_chat_id = settings.get("admin_chat_id", 0)
        self.punishment_forum_channel_id = settings.get("punishment_forum_channel_id", 0)
        self.required_link_role_id = settings.get("required_link_role_id", 0)

        logger.info(f"Application branch initialized with config (db: {self.db_path})")

    async def cog_load(self):
        """Initialize database when branch is loaded."""
        await init_cog_database(self.db_path, APPLICATIONS_SCHEMA, "Application")

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info("Application branch loaded - Registering persistent views")

        # Register persistent views (use same instance to track state)
        self.bot.add_view(self._application_button_view)
        self.bot.add_view(StartCancelView())
        self.bot.add_view(ContinueView())
        self.bot.add_view(PostSubmissionView())
        self.bot.add_view(ManageView())

        await self.ensure_application_message()
        logger.info("Application branch ready")

    @commands.command(name="appstats")
    @is_application_reviewer()
    async def application_stats(self, ctx):
        """Show application statistics (Staff only)"""
        try:
            async with aiosqlite.connect(get_db_path()) as db:
                # Get total applications
                async with db.execute("SELECT COUNT(*) FROM applications") as cursor:
                    total = (await cursor.fetchone())[0]

                # Get status breakdown
                async with db.execute("""
                    SELECT status, COUNT(*)
                    FROM applications
                    GROUP BY status
                """) as cursor:
                    status_counts = {row[0]: row[1] async for row in cursor}

                # Get recent applications (last 7 days)
                async with db.execute("""
                    SELECT COUNT(*)
                    FROM applications
                    WHERE submitted_at >= datetime('now', '-7 days')
                """) as cursor:
                    recent = (await cursor.fetchone())[0]

                # Get average processing time for completed applications
                async with db.execute("""
                    SELECT AVG(julianday(datetime('now')) - julianday(submitted_at))
                    FROM applications
                    WHERE status IN ('accepted', 'denied')
                """) as cursor:
                    avg_days = await cursor.fetchone()
                    avg_processing = avg_days[0] if avg_days[0] else 0

            embed = discord.Embed(
                title="📊 Application Statistics",
                color=discord.Color.blue()
            )

            embed.add_field(
                name="Total Applications",
                value=f"**{total}**",
                inline=True
            )

            embed.add_field(
                name="Last 7 Days",
                value=f"**{recent}**",
                inline=True
            )

            embed.add_field(
                name="Avg. Processing Time",
                value=f"**{avg_processing:.1f}** days",
                inline=True
            )

            status_text = "\n".join([
                f"**{status.title()}:** {count}"
                for status, count in sorted(status_counts.items())
            ])

            embed.add_field(
                name="Status Breakdown",
                value=status_text or "No data",
                inline=False
            )

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error getting application stats: {e}")
            await ctx.send("Failed to retrieve statistics.")

    @commands.command(name="appcleanup")
    @is_application_reviewer()
    async def cleanup_abandoned(self, ctx, days: int = 7):
        """Clean up abandoned applications older than X days (default 7)"""
        try:
            async with aiosqlite.connect(get_db_path()) as db:
                # Find abandoned applications
                async with db.execute("""
                    SELECT channel_id, user_id
                    FROM applications
                    WHERE status = 'in_progress'
                    AND submitted_at < datetime('now', ? || ' days')
                """, (f'-{days}',)) as cursor:
                    abandoned = [row async for row in cursor]

            if not abandoned:
                await ctx.send(f"✅ No abandoned applications found (older than {days} days).")
                return

            cleaned = 0
            for channel_id, user_id in abandoned:
                channel = ctx.guild.get_channel(channel_id)
                if channel:
                    try:
                        await channel.delete(reason=f"Abandoned application cleanup (inactive {days}+ days)")
                        cleaned += 1
                    except discord.HTTPException:
                        pass

                # Update database
                async with aiosqlite.connect(get_db_path()) as db:
                    await db.execute(
                        "UPDATE applications SET status = 'abandoned' WHERE channel_id = ?",
                        (channel_id,)
                    )
                    await db.commit()

            await ctx.send(f"✅ Cleaned up **{cleaned}** abandoned applications (older than {days} days).")
            logger.info(f"{ctx.author} cleaned up {cleaned} abandoned applications")

        except Exception as e:
            logger.error(f"Error cleaning up applications: {e}")
            await ctx.send("Failed to clean up applications.")

    @commands.command(name="applist")
    @is_application_reviewer()
    async def list_applications(self, ctx, status: str = "pending"):
        """List applications by status (pending/in_progress/accepted/denied)"""
        try:
            async with aiosqlite.connect(get_db_path()) as db:
                async with db.execute("""
                    SELECT user_id, channel_id, app_index, submitted_at
                    FROM applications
                    WHERE status = ?
                    ORDER BY submitted_at DESC
                    LIMIT 10
                """, (status,)) as cursor:
                    apps = [row async for row in cursor]

            if not apps:
                await ctx.send(f"No applications found with status: **{status}**")
                return

            embed = discord.Embed(
                title=f"📋 {status.title()} Applications",
                description=f"Showing up to 10 most recent",
                color=discord.Color.blue()
            )

            for user_id, channel_id, app_index, submitted_at in apps:
                user = ctx.guild.get_member(user_id)
                channel = ctx.guild.get_channel(channel_id)

                user_text = user.mention if user else f"<@{user_id}>"
                channel_text = channel.mention if channel else "❌ Deleted"

                embed.add_field(
                    name=f"Application #{app_index}",
                    value=f"**User:** {user_text}\n**Channel:** {channel_text}\n**Date:** {submitted_at[:10]}",
                    inline=False
                )

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error listing applications: {e}")
            await ctx.send("Failed to list applications.")

    async def ensure_application_message(self):
        """Ensure the application button message exists in the channel."""
        try:
            channel = self.bot.get_channel(self.application_channel_id)
            if not channel:
                logger.warning(f"Application channel {self.application_channel_id} not found")
                return

            history = [m async for m in channel.history(limit=10)]
            if not any(m.author == self.bot.user and m.components for m in history):
                await channel.send(
                    embed=discord.Embed(
                        title="👮 Staff Application",
                        description=(
                            "Interested in becoming staff? Click below to start your application!\n\n"
                            "**Requirements:**\n"
                            "• Be an active member of the community\n"
                            "• Have a good understanding of server rules\n"
                            "• Be willing to help other players\n"
                            "• Have time to dedicate to staff duties"
                        ),
                        color=discord.Color.blurple()
                    ),
                    view=self._application_button_view
                )
                logger.info("Created new application button message")
        except Exception as e:
            logger.error(f"Error ensuring application message: {e}")

async def setup(bot):
    await bot.add_cog(ApplicationCog(bot))
