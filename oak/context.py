"""
BranchContext: the immutable bag of services passed to every branch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .database import BranchDatabase
    from .events import BranchEventHandle
    from .interactions import BranchInteractionHandle


@dataclass(frozen=True)
class BranchContext:
    """Everything a branch needs, provided by the framework."""

    bot: Any  # OakBot (avoid circular import)
    id: str  # manifest id (stable, machine-safe)
    name: str  # display name
    config: dict
    db: "BranchDatabase | None"
    logger: logging.Logger
    data_dir: Path
    events: "BranchEventHandle"
    interactions: "BranchInteractionHandle"
