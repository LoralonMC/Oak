"""
Status Channels Branch
Updates voice channels with server status information
"""

from .branch import StatusChannels

async def setup(bot):
    await bot.add_cog(StatusChannels(bot))
