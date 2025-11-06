"""
Application Branch
Handles staff applications with multi-page modals and review workflow
"""

from .branch import ApplicationCog

async def setup(bot):
    await bot.add_cog(ApplicationCog(bot))
