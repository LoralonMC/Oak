"""
Oak — A modular Discord bot framework.

Inspired by Minecraft Paper's plugin architecture.
"""

__version__ = "1.0.0"

from .bot import OakBot
from .branch import OakBranch
from .config import OakConfig
from .context import BranchContext
from .database import BranchDatabase, Migration
from .events import OakEvent
from .tasks import TaskRegistry
from .views import PaginatedEmbedView

__all__ = [
    "__version__",
    "OakBot",
    "OakBranch",
    "OakConfig",
    "BranchContext",
    "BranchDatabase",
    "Migration",
    "OakEvent",
    "PaginatedEmbedView",
    "TaskRegistry",
]
