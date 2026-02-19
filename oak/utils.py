"""Shared utility functions."""

from .constants import EMBED_FIELD_VALUE_MAX


def sanitize_text(text: str, max_length: int = 2000) -> str:
    """Sanitize user input text: truncate, remove null bytes, strip whitespace."""
    if not text:
        return ""
    text = text[:max_length]
    text = text.replace("\x00", "")
    return text.strip()


def truncate_for_embed_field(text: str, suffix: str = "...", max_length: int = EMBED_FIELD_VALUE_MAX) -> str:
    """Truncate text to fit in an embed field value.

    Args:
        text: Text to truncate.
        suffix: String appended when truncation occurs.
        max_length: Maximum allowed length (default: 1024).
    """
    if not text:
        return ""
    if len(suffix) >= max_length:
        return text[:max_length]
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix
