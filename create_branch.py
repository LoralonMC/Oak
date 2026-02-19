#!/usr/bin/env python3
"""
Oak - Create Branch Tool

Script to create a new branch with folder structure and auto-config.

Usage:
    python create_branch.py my_feature "Description of my feature"

Creates:
    branches/my_feature/
    ├── __init__.py       # Empty (framework handles loading)
    ├── branch.yml        # Manifest (framework discovers this)
    ├── branch.py         # Main branch code (OakBranch subclass)
    └── config.yml        # Runtime config (settings only)
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
        print(f"Branch folder already exists: {branch_folder}")
        overwrite = input("Overwrite? (y/N): ").strip().lower()
        if overwrite != "y":
            print("Cancelled")
            return False
    else:
        branch_folder.mkdir(parents=True, exist_ok=True)

    # ========================================================================
    # Create __init__.py (empty — framework uses branch.yml for discovery)
    # ========================================================================
    init_file = branch_folder / "__init__.py"
    with open(init_file, "w") as f:
        f.write("")
    print(f"  Created: {init_file}")

    # ========================================================================
    # Create branch.yml (manifest — framework discovers branches by this file)
    # ========================================================================
    manifest_file = branch_folder / "branch.yml"
    manifest_content = f"""id: {branch_name}
name: {class_name}
version: "1.0.0"
description: {description or f"{class_name} branch"}
main: branch.{class_name}
database: true
"""
    with open(manifest_file, "w") as f:
        f.write(manifest_content)
    print(f"  Created: {manifest_file}")

    # ========================================================================
    # Create branch.py (main branch code — OakBranch subclass)
    # ========================================================================
    branch_file = branch_folder / "branch.py"
    branch_content = f'''"""{class_name} branch — {description or "TODO: add description"}.""\"

import discord
from discord import app_commands
import aiosqlite

from oak import OakBranch
from oak.context import BranchContext


# Database schema (if database: true in branch.yml)
SCHEMA = """
CREATE TABLE IF NOT EXISTS {branch_name}_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


DEFAULT_CONFIG = {{
    "enabled": True,
    "settings": {{
        "example_channel_id": 0,
        "features": {{
            "feature_a": True,
        }},
        "messages": {{
            "welcome": "Welcome to {class_name}!",
            "error": "An error occurred.",
        }},
    }},
}}


class {class_name}(OakBranch):
    """{description or f"{class_name} branch."}""\"

    def __init__(self, ctx: BranchContext):
        super().__init__(ctx)
        self.db_path = str(self.data_dir / "data.db")

        # Access settings via self.setting()
        self.channel_id = self.setting("example_channel_id", default=0)
        self.feature_a = self.setting("features", "feature_a", default=True)

        self.log.info(f"{class_name} branch initialized")

    async def on_enable(self) -> None:
        """Called when the branch is loaded. Initialize database here."""
        if self.db:
            await self.db.initialize(SCHEMA)

    async def on_ready(self) -> None:
        """Called once when the bot is fully connected."""
        self.log.info("{class_name} branch ready")

    async def on_disable(self) -> None:
        """Called when the branch is unloaded. Clean up resources here."""
        self.log.info("{class_name} branch unloaded")

    # ========================================================================
    # Add your commands here
    # ========================================================================

    @app_commands.command(name="{branch_name}", description="Example command")
    async def example_command(self, interaction: discord.Interaction) -> None:
        """Example command — replace with your own."""
        if not self.feature_a:
            await interaction.response.send_message(
                "This feature is disabled.", ephemeral=True
            )
            return

        welcome = self.setting("messages", "welcome", default="Hello!")
        await interaction.response.send_message(welcome)

    @app_commands.command(name="{branch_name}_save", description="Example DB command")
    @app_commands.describe(data="Data to save")
    async def save_data(self, interaction: discord.Interaction, data: str) -> None:
        """Example database command."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO {branch_name}_data (user_id, data) VALUES (?, ?)",
                    (interaction.user.id, data),
                )
                await db.commit()
            await interaction.response.send_message("Data saved!", ephemeral=True)
        except Exception as e:
            self.log.error(f"Error saving data: {{e}}")
            await interaction.response.send_message("Failed to save data.", ephemeral=True)
'''

    with open(branch_file, "w") as f:
        f.write(branch_content)
    print(f"  Created: {branch_file}")

    # ========================================================================
    # Create config.yml (runtime configuration)
    # ========================================================================
    config_file = branch_folder / "config.yml"
    config_content = f"""# Configuration for {class_name} Branch
# {description}

enabled: true

settings:
  example_channel_id: 0

  features:
    feature_a: true

  messages:
    welcome: "Welcome to {class_name}!"
    error: "An error occurred."
"""

    with open(config_file, "w") as f:
        f.write(config_content)
    print(f"  Created: {config_file}")

    # ========================================================================
    # Summary
    # ========================================================================
    print(f"\nCreated branch: {class_name}")
    print(f"\n  {branch_folder}/")
    print("  ├── __init__.py       (empty)")
    print("  ├── branch.yml        (manifest)")
    print("  ├── branch.py         (OakBranch subclass)")
    print("  ├── config.yml        (runtime config)")
    print("  └── data.db           (auto-created on first run)")

    print("\nNext steps:")
    print(f"  1. Edit {config_file} to set channel IDs, role IDs, etc.")
    print(f"  2. Edit {branch_file} to add your commands and logic")
    print(f"  3. Restart the bot or use /load {branch_name}")
    print(f"  4. After changes: /reload {branch_name}")

    return True


def main():
    if len(sys.argv) < 2:
        print("Usage: python create_branch.py <branch_name> [description]")
        print("\nExample:")
        print('  python create_branch.py polls "Community voting system"')
        sys.exit(1)

    branch_name = sys.argv[1].lower()
    description = sys.argv[2] if len(sys.argv) > 2 else ""

    if not branch_name.replace("_", "").isalnum():
        print("Branch name must contain only letters, numbers, and underscores")
        sys.exit(1)

    create_branch(branch_name, description)


if __name__ == "__main__":
    main()
