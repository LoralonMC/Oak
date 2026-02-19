"""Link branch — displays account linking instructions."""

import discord
from discord.ext import commands

from oak import OakBranch
from oak.context import BranchContext

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
            "color": 0xA180D0,
        }
    },
}


class Link(OakBranch):
    """Discord to Minecraft account linking information."""

    def __init__(self, ctx: BranchContext):
        super().__init__(ctx)

    @commands.command(name="link")
    @commands.guild_only()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def link_command(self, ctx: commands.Context) -> None:
        """Display account linking instructions."""
        embed = discord.Embed(
            title=self.setting("embed", "title", default="Account Linking Guide"),
            description=self.setting("embed", "description", default=""),
            color=self.setting("embed", "color", default=0xA180D0),
        )
        await ctx.send(embed=embed)
