"""
Admin Branch
Contains all bot management commands (both prefix and slash commands).
Handles branch loading, reloading, and bot administration.
"""

from .branch import Admin

__all__ = ['Admin', 'setup']

async def setup(bot):
    """Load the Admin branch."""
    await bot.add_cog(Admin(bot))
