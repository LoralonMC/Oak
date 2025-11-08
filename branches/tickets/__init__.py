"""
Tickets Branch
Thread-based support ticket system with category management and staff workflows.

Structure:
- branch.py: Main Tickets class, commands, and background tasks
- views.py: TicketPanelView, TicketControlView (UI components)
- modals.py: CloseReasonModal
- helpers.py: Utility functions and config loading
- config.yml: Category configuration and settings
"""

from .branch import Tickets

__all__ = ['Tickets', 'setup']

async def setup(bot):
    """Load the Tickets branch."""
    await bot.add_cog(Tickets(bot))
