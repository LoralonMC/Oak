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

# Admin Role IDs (Bot management only - !reload, !load, etc.)
ADMIN_ROLE_IDS = get_env_int_list("ADMIN_ROLE_IDS")

# Validate at least one admin role is configured
if not ADMIN_ROLE_IDS or ADMIN_ROLE_IDS == [0]:
    print("WARNING: No valid admin role IDs configured!")
    print("Bot management commands (!reload, !load, etc.) will not be accessible.")
    print("Please update ADMIN_ROLE_IDS in your .env file.")
