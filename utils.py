"""Utility functions for the bot."""

import logging
import yaml
from pathlib import Path

logger = logging.getLogger(__name__)


def deep_merge(base: dict, override: dict) -> dict:
    """
    Deep merge override into base, returning a new dict.

    Keys in override take precedence. Nested dicts are merged recursively.

    Args:
        base: Base dictionary with defaults
        override: Dictionary with overrides

    Returns:
        Merged dictionary
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_branch_config(config_path: Path, default_config: dict, branch_name: str) -> dict:
    """
    Load branch configuration from YAML file with deep merge against defaults.

    Missing keys in the config file will fall back to default values.

    Args:
        config_path: Path to config.yml file
        default_config: Default configuration dictionary
        branch_name: Name of the branch (for logging)

    Returns:
        Merged configuration with defaults for missing keys
    """
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f) or {}
            logger.info(f"Loaded config for {branch_name}")
            return deep_merge(default_config, config)
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
