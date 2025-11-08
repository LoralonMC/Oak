"""
Status Channels Branch
Updates voice channels with server status information.
"""

from .branch import StatusChannels

__all__ = ['StatusChannels', 'setup']

async def setup(bot):
    """Load the StatusChannels branch."""
    await bot.add_cog(StatusChannels(bot))
