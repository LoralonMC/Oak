import discord
from discord.ext import commands
from pathlib import Path
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_CONFIG = {
    "enabled": True,
    "version": "1.0.0",
    "settings": {
        "embed": {
            "title": "Account Linking Guide",
            "description": (
                "By linking your account, you get your in-game ranks and username applied on Discord.\n\n"
                "**How to link:**\n"
                "1. Log in to the Minecraft server\n"
                "2. Type `/discord link` in chat\n"
                "3. Send the code you receive to the Discord bot\n\n"
                "Once linked, your Discord roles will sync with your in-game ranks."
            ),
            "color": 0xA180D0  # Purple color
        }
    }
}

class Link(commands.Cog):
    """Discord to Minecraft account linking information."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # Load config
        self.config = self.load_config()

        # Load embed settings
        embed_settings = self.config.get("settings", {}).get("embed", {})
        self.embed_title: str = embed_settings.get("title", DEFAULT_CONFIG["settings"]["embed"]["title"])
        self.embed_description: str = embed_settings.get("description", DEFAULT_CONFIG["settings"]["embed"]["description"])
        self.embed_color: int = embed_settings.get("color", DEFAULT_CONFIG["settings"]["embed"]["color"])

        logger.info("Link branch initialized")

    def load_config(self) -> Dict[str, Any]:
        """Load config from config.yml in this branch's folder."""
        from utils import load_branch_config
        config_path = Path(__file__).parent / "config.yml"
        return load_branch_config(config_path, DEFAULT_CONFIG, "Link")

    @commands.command(name="link")
    async def link_command(self, ctx: commands.Context) -> None:
        """Display account linking instructions."""
        embed = discord.Embed(
            title=self.embed_title,
            description=self.embed_description,
            color=self.embed_color
        )
        await ctx.send(embed=embed)
