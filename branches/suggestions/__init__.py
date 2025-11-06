"""
Suggestions Branch
Handles user suggestions with voting and management
"""

from .branch import Suggestions, DummyView

async def setup(bot):
    await bot.add_cog(Suggestions(bot))
