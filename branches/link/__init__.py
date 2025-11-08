"""
Link Branch
Handles linking Discord accounts to Minecraft accounts.
"""

from .branch import Link

__all__ = ['Link', 'setup']

async def setup(bot):
    """Load the Link branch."""
    await bot.add_cog(Link(bot))
