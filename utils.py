"""Utility functions for the bot."""

import re
import logging
import yaml
from pathlib import Path
from typing import Dict, Any
from constants import MIN_AGE_DISCORD_TOS, MAX_AGE_REASONABLE

logger = logging.getLogger(__name__)


def load_branch_config(config_path: Path, default_config: Dict[str, Any], branch_name: str) -> Dict[str, Any]:
    """
    Load branch configuration from YAML file with fallback to defaults.

    Args:
        config_path: Path to config.yml file
        default_config: Default configuration dictionary
        branch_name: Name of the branch (for logging)

    Returns:
        Loaded configuration or default config if file doesn't exist
    """
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f) or {}
            logger.info(f"Loaded config for {branch_name}")
            return config
        except Exception as e:
            logger.error(f"Failed to load config for {branch_name}: {e}")

    return default_config


def sanitize_text(text: str, max_length: int = 2000) -> str:
    """
    Sanitize user input text.

    Args:
        text: The text to sanitize
        max_length: Maximum allowed length

    Returns:
        Sanitized text
    """
    if not text:
        return ""

    # Truncate to max length
    text = text[:max_length]

    # Remove null bytes
    text = text.replace('\x00', '')

    return text.strip()


def validate_minecraft_username(username: str) -> bool:
    """
    Validate a Minecraft username.

    Args:
        username: The username to validate

    Returns:
        True if valid, False otherwise
    """
    if not username:
        return False

    # Minecraft usernames are 3-16 characters, alphanumeric and underscores
    pattern = r'^[a-zA-Z0-9_]{3,16}$'
    return bool(re.match(pattern, username))


def validate_age(age_str: str) -> bool:
    """
    Validate an age input.

    Args:
        age_str: The age string to validate

    Returns:
        True if valid, False otherwise
    """
    try:
        age = int(age_str)
        return MIN_AGE_DISCORD_TOS <= age <= MAX_AGE_REASONABLE
    except ValueError:
        return False


def truncate_text(text: str, limit: int = 1024, suffix: str = '...') -> str:
    """
    Truncate text to a specified limit with a suffix.

    Args:
        text: The text to truncate
        limit: Maximum length
        suffix: Suffix to add if truncated

    Returns:
        Truncated text
    """
    if not text:
        return ""

    if len(text) <= limit:
        return text

    return text[:limit - len(suffix)] + suffix


def format_duration(seconds: int) -> str:
    """
    Format seconds into a human-readable duration.

    Args:
        seconds: Number of seconds

    Returns:
        Formatted string like "2h 30m"
    """
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if hours > 0:
        return f"{int(hours)}h {int(minutes)}m"
    elif minutes > 0:
        return f"{int(minutes)}m"
    else:
        return f"{int(seconds)}s"


def is_valid_url(url: str) -> bool:
    """
    Check if a string is a valid URL.

    Args:
        url: The URL to check

    Returns:
        True if valid, False otherwise
    """
    pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain...
        r'localhost|'  # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return bool(pattern.match(url))


def validate_yes_no(response: str) -> bool:
    """
    Check if response is a valid yes/no answer.

    Args:
        response: The response to check

    Returns:
        True if valid yes/no, False otherwise
    """
    normalized = response.strip().lower()
    valid_responses = {'yes', 'no', 'y', 'n', 'yeah', 'nah', 'yep', 'nope'}
    return normalized in valid_responses


def validate_rating(rating_str: str, min_val: int = 1, max_val: int = 5) -> bool:
    """
    Validate a numeric rating.

    Args:
        rating_str: The rating string to validate
        min_val: Minimum valid value
        max_val: Maximum valid value

    Returns:
        True if valid rating, False otherwise
    """
    try:
        rating = int(rating_str.strip())
        return min_val <= rating <= max_val
    except ValueError:
        return False


def validate_time_commitment(time_str: str) -> bool:
    """
    Validate time commitment response.

    Args:
        time_str: The time string (e.g., "2-3 hours", "1 hour", "30 minutes")

    Returns:
        True if looks like a valid time commitment
    """
    time_str = time_str.lower().strip()
    # Check for common time patterns
    patterns = [
        r'\d+\s*(hour|hr|h)',  # "2 hours", "2hr", "2h"
        r'\d+\s*(minute|min|m)',  # "30 minutes", "30min", "30m"
        r'\d+-\d+\s*(hour|hr|h)',  # "2-3 hours"
        r'(few|couple|several)',  # "a few hours"
    ]
    return any(re.search(pattern, time_str) for pattern in patterns) or len(time_str) >= 5


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
