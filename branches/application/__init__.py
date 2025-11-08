"""
Application Branch
Handles staff applications with multi-page modals and review workflow.

Structure:
- branch.py: Main Application class and commands
- modals.py: ApplicationModal, DeclineReasonModal
- views.py: All view classes (buttons, etc.)
- helpers.py: Utility functions and config loading
- background_check.py: MySQL/Plan integration for playtime checks
"""

from .branch import Application

__all__ = ['Application', 'setup']

async def setup(bot):
    """Load the Application branch."""
    await bot.add_cog(Application(bot))
