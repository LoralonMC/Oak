"""
Oak configuration: environment loading, branch config merge.
"""

import copy
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .errors import ConfigError

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


def write_branch_enabled(branch_dir: Path, enabled: bool) -> None:
    """Set the ``enabled`` flag in a branch's config.yml.

    Writes to a temp file in the same directory then atomically replaces
    the target, so a crash mid-write can't truncate the file (branch
    configs may contain secrets — partial writes would be very bad).
    """
    config_path = branch_dir / "config.yml"
    try:
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}
        data["enabled"] = enabled

        # Write to a temp file in the same directory then os.replace —
        # atomic on POSIX and Windows (same-volume rename).
        fd, tmp_path = tempfile.mkstemp(
            prefix=".config.", suffix=".tmp", dir=str(branch_dir)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, default_flow_style=False)
            os.replace(tmp_path, config_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.error(f"Failed to write enabled={enabled} to {config_path}: {e}")
        raise


class OakConfig:
    """Loads and validates environment variables for the bot."""

    def __init__(self, env_path: str = ".env"):
        load_dotenv(env_path)
        self.token = self._require("DISCORD_TOKEN")
        self.guild_id = self._require_int("GUILD_ID")
        self.command_prefix = os.getenv("COMMAND_PREFIX", "!")
        self.dev_mode = os.getenv("DEV_MODE", "").lower() in ("true", "1", "yes")
        self.audit_log_channel = int(os.getenv("AUDIT_LOG_CHANNEL", "0"))
        # Channel for forwarded error logs (0 = disabled). Separate from the
        # audit channel: audit is "who did what", this is "what broke".
        self.error_log_channel = int(os.getenv("ERROR_LOG_CHANNEL", "0"))
        self.error_log_level = os.getenv("ERROR_LOG_LEVEL", "ERROR").upper()
        # Clamp to safe ranges — a negative interval would burn CPU in a tight
        # asyncio.sleep loop; a zero max-count would delete every backup
        # immediately after creating it.
        self.db_backup_interval = max(0.0, float(os.getenv("DB_BACKUP_INTERVAL", "0")))
        self.db_backup_max_count = max(1, int(os.getenv("DB_BACKUP_MAX_COUNT", "3")))
        self._validate()

    def _require(self, key: str) -> str:
        value = os.getenv(key)
        if not value:
            msg = f"Missing required environment variable: {key}. Please add {key} to your .env file"
            logger.critical(msg)
            raise ConfigError(msg)
        return value

    def _require_int(self, key: str) -> int:
        raw = self._require(key)
        try:
            return int(raw)
        except ValueError:
            msg = f"{key} must be a valid integer, got: {raw}"
            logger.critical(msg)
            raise ConfigError(msg)

    def _validate(self) -> None:
        placeholders = {"your_bot_token_here", "your_token_here", "placeholder", ""}
        if self.token in placeholders:
            msg = "DISCORD_TOKEN is still set to a placeholder value!"
            logger.critical(msg)
            raise ConfigError(msg)
        if self.guild_id == 0:
            msg = "GUILD_ID is still set to 0 (placeholder)!"
            logger.critical(msg)
            raise ConfigError(msg)

    def get(self, key: str, default: Any = None) -> Any:
        """Get an optional environment variable."""
        return os.getenv(key, default)
