"""
Oak — A modular Discord bot framework.

Inspired by Minecraft Paper's plugin architecture.
"""

from .bot import OakBot
from .branch import OakBranch
from .config import OakConfig
from .context import BranchContext
from .database import BranchDatabase, Migration
from .events import OakEvent

__all__ = [
    "OakBot",
    "OakBranch",
    "OakConfig",
    "BranchContext",
    "BranchDatabase",
    "Migration",
    "OakEvent",
]
