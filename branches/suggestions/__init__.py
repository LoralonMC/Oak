"""
Suggestions Branch
Handles user suggestions with voting and management.

Structure:
- branch.py: Main Suggestions class and on_message handler
- views.py: DummyView, ManageSuggestionView (Discord UI components)
- modals.py: StatusModal (approval/denial forms)
- handlers.py: handle_vote_button, handle_manage_button (button logic)
- helpers.py: Utility functions and config loading
"""

from .branch import Suggestions
from .views import DummyView

__all__ = ['Suggestions', 'DummyView', 'setup']

async def setup(bot):
    """Load the Suggestions branch."""
    await bot.add_cog(Suggestions(bot))
