#!/usr/bin/env python3
"""
Oak - Create Branch Tool

Script to create a new branch with folder structure and auto-config.

Usage:
    python create_branch.py my_feature "Description of my feature"

Creates:
    branches/my_feature/
    ├── __init__.py
    ├── branch.py         # Main branch code
    ├── config.yml        # Auto-generated config
    └── data.db           # Database (auto-created on first run)
"""

import sys
from pathlib import Path


def create_branch(branch_name: str, description: str = ""):
    """Create a new branch with folder structure."""

    # Convert to PascalCase for class name
    class_name = "".join(word.capitalize() for word in branch_name.split("_"))

    # Create branch folder
    branch_folder = Path(f"branches/{branch_name}")

    if branch_folder.exists():
        print(f"❌ Branch folder already exists: {branch_folder}")
        overwrite = input("Overwrite? (y/N): ").strip().lower()
        if overwrite != "y":
            print("❌ Cancelled")
            return False
    else:
        branch_folder.mkdir(parents=True, exist_ok=True)

    # ========================================================================
    # Create __init__.py (makes it a package)
    # ========================================================================
    init_file = branch_folder / "__init__.py"
    init_content = f'''"""
{class_name} Branch
{description}
"""

from .branch import {class_name}

async def setup(bot):
    await bot.add_cog({class_name}(bot))
'''

    with open(init_file, "w") as f:
        f.write(init_content)

    print(f"✅ Created: {init_file}")

    # ========================================================================
    # Create branch.py (main branch code)
    # ========================================================================
    branch_file = branch_folder / "branch.py"
    branch_content = f'''"""
{class_name} Branch Implementation
{description}
"""

import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import logging
from pathlib import Path
import yaml
from database import init_branch_database

logger = logging.getLogger(__name__)


# Database schema for this branch (if you need a database)
DATABASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS {branch_name}_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


# Default configuration for this branch
DEFAULT_CONFIG = {{
    "enabled": True,
    "version": "1.0.0",
    "settings": {{
        # Add your default settings here
        "example_channel_id": 0,

        "features": {{
            "feature_a": True,
            "feature_b": False,
        }},

        "limits": {{
            "max_items": 10,
            "cooldown_seconds": 60,
        }},

        "messages": {{
            "welcome": "Welcome to {class_name}!",
            "error": "An error occurred.",
        }}
    }}
}}


class {class_name}(commands.Cog):
    """{description or f"Branch for {branch_name} functionality."}"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # Set database path (in this branch's folder)
        self.db_path = str(Path(__file__).parent / "data.db")

        # Load config
        self.config = self.load_config()

        # Access settings
        settings = self.config.get("settings", {{}})
        self.channel_id: int = settings.get("example_channel_id", 0)
        self.max_items: int = settings.get("limits", {{}}).get("max_items", 10)

        # Feature flags
        features = settings.get("features", {{}})
        self.feature_a_enabled: bool = features.get("feature_a", True)

        logger.info(f"{class_name} branch initialized (db: {{self.db_path}})")

    async def cog_load(self) -> None:
        """
        Initialize database when branch is loaded.

        Note: cog_load() is a Discord.py lifecycle method (part of the Cog API).
        It's called automatically when the branch is loaded by the bot.
        """
        # Only initialize if you need a database
        # Comment out if you don't need database functionality
        await init_branch_database(self.db_path, DATABASE_SCHEMA, "{class_name}")

    def load_config(self) -> dict:
        """Load config from config.yml in this branch's folder."""
        from utils import load_branch_config
        config_path = Path(__file__).parent / "config.yml"
        return load_branch_config(config_path, DEFAULT_CONFIG, "{class_name}")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Called when the branch is ready."""
        logger.info(f"{class_name} branch ready")

    # ========================================================================
    # Add your commands here
    # ========================================================================

    @app_commands.command(name="{branch_name}_example", description="Example command - replace with your own")
    async def example_command(self, interaction: discord.Interaction) -> None:
        """Example command - replace with your own."""

        # Check if feature is enabled
        if not self.feature_a_enabled:
            await interaction.response.send_message("This feature is disabled in config.", ephemeral=True)
            return

        # Get message from config
        messages = self.config.get("settings", {{}}).get("messages", {{}})
        welcome = messages.get("welcome", "Hello!")

        await interaction.response.send_message(welcome)

    # Example database operation (if using database)
    @app_commands.command(name="{branch_name}_save", description="Example database save command")
    @app_commands.describe(data="Data to save")
    async def save_data(self, interaction: discord.Interaction, data: str) -> None:
        """Example database save command."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO {branch_name}_data (user_id, data) VALUES (?, ?)",
                    (interaction.user.id, data)
                )
                await db.commit()
            await interaction.response.send_message("Data saved!", ephemeral=True)
        except Exception as e:
            logger.error(f"Error saving data: {{e}}")
            await interaction.response.send_message("Failed to save data.", ephemeral=True)

    def cog_unload(self) -> None:
        """
        Called when the branch is unloaded.

        Note: cog_unload() is a Discord.py lifecycle method (part of the Cog API).
        Use this to clean up resources (cancel tasks, close connections, etc.)
        """
        logger.info(f"{class_name} branch unloaded")
'''

    with open(branch_file, "w") as f:
        f.write(branch_content)

    print(f"✅ Created: {branch_file}")

    # ========================================================================
    # Create config.yml (default configuration)
    # ========================================================================
    config_file = branch_folder / "config.yml"
    config_content = f"""# Configuration for {class_name} Branch
# {description}

# Set to false to disable this branch
enabled: true

# Version of this branch's config
version: "1.0.0"

settings:
  # Channel ID where this branch operates
  example_channel_id: 0

  # Feature flags (toggle functionality)
  features:
    feature_a: true
    feature_b: false

  # Limits and restrictions
  limits:
    max_items: 10
    cooldown_seconds: 60

  # Messages and templates
  messages:
    welcome: "Welcome to {class_name}!"
    error: "An error occurred."

# Add more settings as needed
"""

    with open(config_file, "w") as f:
        f.write(config_content)

    print(f"✅ Created: {config_file}")

    # ========================================================================
    # Summary
    # ========================================================================
    print(f"\n{'='*60}")
    print(f"✨ Successfully created branch: {class_name}")
    print(f"{'='*60}")
    print(f"\n📁 Folder structure:")
    print(f"   {branch_folder}/")
    print(f"   ├── __init__.py       (package setup)")
    print(f"   ├── branch.py         (main code)")
    print(f"   ├── config.yml        (configuration)")
    print(f"   └── data.db           (database - auto-created on first run)")

    print(f"\n📝 Configuration:")
    print(f"   Edit {config_file}")
    print(f"   - Set channel IDs and role IDs")
    print(f"   - Toggle features on/off")
    print(f"   - Customize messages")

    print(f"\n💾 Database:")
    print(f"   - Database file: {branch_folder}/data.db")
    print(f"   - Auto-created on first bot run")
    print(f"   - Automatically gitignored")
    print(f"   - Modify DATABASE_SCHEMA in branch.py to add tables")
    print(f"   - Comment out the database init in cog_load() if you don't need a database")
    print(f"   - Note: cog_load() is a Discord.py Cog lifecycle method")

    print(f"\n🚀 Next steps:")
    print(f"   1. Edit {config_file} to configure your branch")
    print(f"   2. Edit {branch_file} to add your functionality")
    print(f"   3. Restart the bot (it will auto-load) or use:")
    print(f"      /load {branch_name}")
    print(f"\n   4. To reload after changes:")
    print(f"      /reload {branch_name}")
    print(f"\n   5. To disable:")
    print(f"      Edit config.yml and set enabled: false")

    return True


def main():
    if len(sys.argv) < 2:
        print("Usage: python create_branch.py <branch_name> [description]")
        print("\nExample:")
        print('  python create_branch.py polls "Community voting system"')
        print("\nCreates:")
        print("  branches/polls/")
        print("  ├── __init__.py")
        print("  ├── branch.py")
        print("  ├── config.yml")
        print("  └── data.db  (auto-created on first run)")
        sys.exit(1)

    branch_name = sys.argv[1].lower()
    description = sys.argv[2] if len(sys.argv) > 2 else ""

    # Validate branch name
    if not branch_name.replace("_", "").isalnum():
        print("❌ Branch name must contain only letters, numbers, and underscores")
        sys.exit(1)

    create_branch(branch_name, description)


if __name__ == "__main__":
    main()
