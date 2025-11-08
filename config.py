"""
Global configuration loader for Discord Bot.
Loads environment variables from .env file.
"""
from dotenv import load_dotenv
import os
import sys

load_dotenv()

def get_env(key: str, required: bool = True, default=None):
    """Safely get environment variable with validation."""
    value = os.getenv(key, default)
    if required and value is None:
        print(f"ERROR: Missing required environment variable: {key}")
        print(f"Please add {key} to your .env file")
        sys.exit(1)
    return value

def get_env_int(key: str, required: bool = True, default=None):
    """Get environment variable as integer."""
    value = get_env(key, required, default)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        print(f"ERROR: Environment variable {key} must be a valid integer, got: {value}")
        sys.exit(1)

def get_env_int_list(key: str, required: bool = True, default=None):
    """Get environment variable as list of integers."""
    value = get_env(key, required, default)
    if value is None:
        return []
    try:
        return [int(rid.strip()) for rid in value.split(",") if rid.strip()]
    except ValueError:
        print(f"ERROR: Environment variable {key} must be comma-separated integers, got: {value}")
        sys.exit(1)

# ============================================================================
# Global Bot Configuration (from .env)
# ============================================================================
# Discord Bot Token (REQUIRED)
DISCORD_TOKEN = get_env("DISCORD_TOKEN")

# Validate token is not a placeholder
PLACEHOLDER_TOKENS = ["your_bot_token_here", "your_token_here", "placeholder", ""]
if DISCORD_TOKEN in PLACEHOLDER_TOKENS:
    print("ERROR: DISCORD_TOKEN is still set to a placeholder value!")
    print("Please update your .env file with a real Discord bot token.")
    print("Get one from: https://discord.com/developers/applications")
    sys.exit(1)

# Guild ID (Global setting)
GUILD_ID = get_env_int("GUILD_ID")

# Validate Guild ID is not placeholder
if GUILD_ID == 0:
    print("ERROR: GUILD_ID is still set to 0 (placeholder)!")
    print("Please update your .env file with your Discord server ID.")
    sys.exit(1)

# Note: Bot admin commands (/reload, /load, etc.) now use Discord's built-in
# Administrator permission via @app_commands.default_permissions(administrator=True)
# No custom role IDs needed for bot management.

# ============================================================================
# Configuration Validation Helpers
# ============================================================================

def validate_channel_id(channel_id: int, name: str = "channel_id") -> bool:
    """
    Validate a Discord channel ID.

    Args:
        channel_id: The channel ID to validate
        name: Name of the setting (for error messages)

    Returns:
        True if valid, False otherwise
    """
    if not isinstance(channel_id, int):
        print(f"ERROR: {name} must be an integer, got {type(channel_id)}")
        return False

    if channel_id != 0 and (channel_id < 0 or channel_id > 2**63):
        print(f"ERROR: {name} must be a valid Discord ID (got {channel_id})")
        return False

    return True


def validate_role_ids(role_ids: list, name: str = "role_ids") -> bool:
    """
    Validate a list of Discord role IDs.

    Args:
        role_ids: List of role IDs to validate
        name: Name of the setting (for error messages)

    Returns:
        True if valid, False otherwise
    """
    if not isinstance(role_ids, list):
        print(f"ERROR: {name} must be a list, got {type(role_ids)}")
        return False

    for i, role_id in enumerate(role_ids):
        if not isinstance(role_id, int):
            print(f"ERROR: {name}[{i}] must be an integer, got {type(role_id)}")
            return False

        if role_id != 0 and (role_id < 0 or role_id > 2**63):
            print(f"ERROR: {name}[{i}] must be a valid Discord ID (got {role_id})")
            return False

    return True


def validate_config_dict(config: dict, required_keys: list = None) -> bool:
    """
    Validate a configuration dictionary.

    Args:
        config: Configuration dictionary to validate
        required_keys: List of required keys

    Returns:
        True if valid, False otherwise
    """
    if not isinstance(config, dict):
        print(f"ERROR: Config must be a dictionary, got {type(config)}")
        return False

    if required_keys:
        missing_keys = [key for key in required_keys if key not in config]
        if missing_keys:
            print(f"ERROR: Missing required config keys: {', '.join(missing_keys)}")
            return False

    return True
