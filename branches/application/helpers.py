"""
Application Helper Functions
Shared utility functions for the application system.
"""

import discord
import logging
from oak.constants import EMBED_MAX_FIELDS, EMBED_TOTAL_MAX
from oak.utils import truncate_for_embed_field

logger = logging.getLogger(__name__)


def get_embed_colors(config: dict) -> dict:
    """Get embed colors from config.

    Args:
        config: Application configuration dictionary

    Returns:
        Dict mapping color names to integer values
    """
    ui_settings = config.get("settings", {}).get("ui", {})
    embed_colors = ui_settings.get("embed_colors", {})
    return {
        "info": embed_colors.get("info", 0x5865F2),
        "success": embed_colors.get("success", 0x57F287),
        "warning": embed_colors.get("warning", 0xFEE75C),
        "error": embed_colors.get("error", 0xED4245)
    }


def get_application_questions(config: dict) -> list:
    """Get application questions from config.

    Args:
        config: Application configuration dictionary

    Returns:
        List of question dicts
    """
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


def paginate_application_embed(applicant, answers, questions: list, colors: dict = None):
    """
    Returns a list of embeds, paginated by Discord's field and character limits.

    Args:
        applicant: Discord member who applied (may be None if user left the server)
        answers: List of application answers
        questions: List of question dicts
        colors: Embed colors dict (from get_embed_colors)

    Returns:
        List of paginated embeds
    """
    if colors is None:
        colors = {
            "info": 0x5865F2, "success": 0x57F287,
            "warning": 0xFEE75C, "error": 0xED4245
        }

    # Handle mismatch between questions and answers (legacy applications)
    # Use the minimum to avoid index errors
    total_items = min(len(questions), len(answers))

    # If there are more answers than questions, add generic labels
    if len(answers) > len(questions):
        logger.warning(f"Application has {len(answers)} answers but only {len(questions)} questions configured. Truncating to match.")
    elif len(questions) > len(answers):
        logger.warning(f"Application has {len(answers)} answers but {len(questions)} questions configured. Some questions will be skipped.")

    def make_embed(fields, page_num, total_pages):
        # Handle applicant=None safely (user left the server)
        # m4: Use display_name instead of mention in embed title
        if applicant:
            title = f"Application from {applicant.display_name}"
        else:
            title = "Application from Unknown Applicant"

        embed = discord.Embed(
            title=title,
            color=colors["info"]
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
    safety_counter = 0
    max_iterations = total_items + 100  # Safety limit to prevent infinite loops
    while i < total_items:
        safety_counter += 1
        if safety_counter > max_iterations:
            logger.error("Pagination safety limit reached, breaking to prevent infinite loop")
            break

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

            # Always add at least one field per page to guarantee forward progress
            if fields_in_this_embed > 0 and (fields_in_this_embed >= EMBED_MAX_FIELDS or char_count + added_chars > EMBED_TOTAL_MAX):
                break

            fields.append((label, value))
            char_count += added_chars
            fields_in_this_embed += 1
            i += 1

        all_embeds.append(fields)

    total_pages = len(all_embeds)
    return [make_embed(fields, idx+1, total_pages) for idx, fields in enumerate(all_embeds)]


def is_staff(member, reviewer_role_ids: list) -> bool:
    """Check if member has application reviewer permissions.

    Args:
        member: Discord member
        reviewer_role_ids: List of reviewer role IDs

    Returns:
        True if member has reviewer permissions
    """
    return any(role.id in reviewer_role_ids for role in getattr(member, "roles", []))


def check_application_answer_quality(question: str, answer: str) -> tuple[bool, str]:
    """
    Check if an application answer is of sufficient quality.

    Simple validation that only catches empty answers and obvious spam.
    Server owners can review answers themselves and decide what's acceptable.

    Args:
        question: The question that was asked
        answer: The answer provided

    Returns:
        Tuple of (is_valid, error_message)
    """
    answer = answer.strip()

    # Check for empty answers
    if len(answer) == 0:
        return False, "Please provide an answer."

    # Check if it's just repeated characters (spam like "aaaaa" or ".....")
    if len(answer) >= 3 and len(set(answer.replace(' ', ''))) < 2:
        return False, "Please provide a real answer."

    # All other answers are accepted - let staff review them
    return True, ""
