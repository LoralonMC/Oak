"""Shared utility functions."""


def sanitize_text(text: str, max_length: int = 2000) -> str:
    """Sanitize user input text: truncate, remove null bytes, strip whitespace."""
    if not text:
        return ""
    text = text[:max_length]
    text = text.replace("\x00", "")
    return text.strip()
