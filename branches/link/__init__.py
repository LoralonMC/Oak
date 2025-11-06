"""
Link Branch
Handles linking Discord accounts to Minecraft accounts
"""

from .branch import Link

async def setup(bot):
    await bot.add_cog(Link(bot))
