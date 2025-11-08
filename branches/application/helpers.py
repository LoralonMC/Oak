"""
Application Helper Functions
Shared utility functions for the application system.
"""

import discord
import yaml
import logging
from pathlib import Path
from constants import (
    EMBED_MAX_FIELDS,
    EMBED_TOTAL_MAX,
    truncate_for_embed_field
)

logger = logging.getLogger(__name__)


def get_embed_colors():
    """Get embed colors from config."""
    config = get_application_config()
    ui_settings = config.get("settings", {}).get("ui", {})
    embed_colors = ui_settings.get("embed_colors", {})
    return {
        "info": embed_colors.get("info", 0x5865F2),       # Blurple
        "success": embed_colors.get("success", 0x57F287),  # Green
        "warning": embed_colors.get("warning", 0xFEE75C),  # Yellow
        "error": embed_colors.get("error", 0xED4245)       # Red
    }


def get_db_path():
    """Get the database path for this branch."""
    return str(Path(__file__).parent / "data.db")


def get_application_config():
    """Load application config from config.yml."""
    config_path = Path(__file__).parent / "config.yml"
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
        return config
    except Exception as e:
        logger.error(f"Failed to load application config: {e}")
        return {}


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


def paginate_application_embed(applicant, answers, get_questions_func=None):
    """
    Returns a list of embeds, paginated by Discord's field and character limits.

    Args:
        applicant: Discord member who applied
        answers: List of application answers
        get_questions_func: Function to get questions (optional, uses default if None)

    Returns:
        List of paginated embeds
    """
    questions = get_questions_func() if get_questions_func else get_application_questions()

    # Handle mismatch between questions and answers (legacy applications)
    # Use the minimum to avoid index errors
    total_items = min(len(questions), len(answers))

    # If there are more answers than questions, add generic labels
    if len(answers) > len(questions):
        logger.warning(f"Application has {len(answers)} answers but only {len(questions)} questions configured. Truncating to match.")
    elif len(questions) > len(answers):
        logger.warning(f"Application has {len(answers)} answers but {len(questions)} questions configured. Some questions will be skipped.")

    def make_embed(fields, page_num, total_pages):
        embed = discord.Embed(
            title=f"Application from {applicant.mention if applicant else f'<@{applicant.id}>'}",
            color=get_embed_colors()["info"]
        )
        if applicant:
            embed.set_author(name=str(applicant), icon_url=applicant.display_avatar.url)
            embed.set_thumbnail(url=applicant.display_avatar.url)
        for label, value in fields:
            embed.add_field(name=label, value=value, inline=False)
        if total_pages > 1:
            embed.set_footer(text=f"Page {page_num} of {total_pages}")
        return embed

    # Gather fields for each embed, respecting Discord's field and character limits
    all_embeds = []
    i = 0
    while i < total_items:
        fields = []
        char_count = 0
        fields_in_this_embed = 0

        while i < total_items and fields_in_this_embed < EMBED_MAX_FIELDS and char_count < EMBED_TOTAL_MAX:
            # Safely access question label with fallback
            label = questions[i]['label'] if i < len(questions) else f"Question {i+1}"
            answer = answers[i] if i < len(answers) else "*No response*"
            value = truncate_for_embed_field(answer) if answer else "*No response*"

            # Add size of this field (label + value + field overhead)
            added_chars = len(label) + len(value) + 50  # 50 is a fudge factor for formatting

            if fields_in_this_embed >= EMBED_MAX_FIELDS or char_count + added_chars > EMBED_TOTAL_MAX:
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


def is_application_reviewer():
    """
    Decorator to check if user has application reviewer permissions.

    Returns:
        commands.check decorator
    """
    from discord.ext import commands

    async def predicate(ctx):
        config = get_application_config()
        reviewer_role_ids = config.get("settings", {}).get("reviewer_role_ids", [])
        user_role_ids = [role.id for role in ctx.author.roles]
        return any(role_id in reviewer_role_ids for role_id in user_role_ids)

    return commands.check(predicate)
