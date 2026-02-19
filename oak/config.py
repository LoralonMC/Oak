"""
Oak configuration: environment loading, branch config merge.
"""

import copy
import logging
import os
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base, returning a new dict.

    Uses deepcopy to prevent shared mutable references between
    the returned dict and the originals.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_branch_config(branch_dir: Path, default_config: dict, branch_id: str) -> dict:
    """
    Load a branch's config.yml and deep-merge against defaults.

    Args:
        branch_dir: The branch's directory (containing config.yml).
        default_config: Default configuration dictionary from the branch.
        branch_id: Branch identifier (for logging).

    Returns:
        Merged configuration with defaults for missing keys.
    """
    config_path = branch_dir / "config.yml"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            logger.info(f"Loaded config for {branch_id}")
            return deep_merge(default_config, config)
        except Exception as e:
            logger.error(f"Failed to load config for {branch_id}: {e}")
    return copy.deepcopy(default_config)


class OakConfig:
    """Loads and validates environment variables for the bot."""

    def __init__(self, env_path: str = ".env"):
        load_dotenv(env_path)
        self.token = self._require("DISCORD_TOKEN")
        self.guild_id = self._require_int("GUILD_ID")
        self._validate()

    def _require(self, key: str) -> str:
        value = os.getenv(key)
        if not value:
            print(f"ERROR: Missing required environment variable: {key}")
            print(f"Please add {key} to your .env file")
            sys.exit(1)
        return value

    def _require_int(self, key: str) -> int:
        raw = self._require(key)
        try:
            return int(raw)
        except ValueError:
            print(f"ERROR: {key} must be a valid integer, got: {raw}")
            sys.exit(1)

    def _validate(self) -> None:
        placeholders = {"your_bot_token_here", "your_token_here", "placeholder", ""}
        if self.token in placeholders:
            print("ERROR: DISCORD_TOKEN is still set to a placeholder value!")
            sys.exit(1)
        if self.guild_id == 0:
            print("ERROR: GUILD_ID is still set to 0 (placeholder)!")
            sys.exit(1)

    def get(self, key: str, default: Any = None) -> Any:
        """Get an optional environment variable."""
        return os.getenv(key, default)
