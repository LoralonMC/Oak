import discord
from discord.ext import commands

class Link(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="link")
    async def link_command(self, ctx):
        embed = discord.Embed(
            title="Account Linking Guide",
            description=(
                "By linking your account, you get your in-game ranks and username applied on Discord.\n\n"
                "**How to link:**\n"
                "1. Log in to the Minecraft server\n"
                "2. Type `/discord link` in chat\n"
                "3. Send the code you receive to the Discord bot\n\n"
                "Once linked, your Discord roles will sync with your in-game ranks."
            ),
            color=discord.Color(int("A180D0", 16))
        )
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Link(bot))
