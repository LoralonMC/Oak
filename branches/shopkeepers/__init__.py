"""
Shopkeepers Branch
CSV-based trade log importer and price analysis system for Minecraft shopkeeper data.

Structure:
- branch.py: Main Shopkeepers class, commands, and background tasks
- nbt_parser.py: NBT metadata parser for item identification
- helpers.py: Utility functions and config loading
- config.yml: Currency configuration and settings
"""

from .branch import Shopkeepers

__all__ = ['Shopkeepers', 'setup']

async def setup(bot):
    """Load the Shopkeepers branch."""
    await bot.add_cog(Shopkeepers(bot))
