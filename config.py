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
