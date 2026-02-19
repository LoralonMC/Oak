"""
InteractionRouter: namespaced custom_ids with oak: prefix.

Format: "oak:{branch}:{action}" or "oak:{branch}:{action}:{value}"
Max 100 chars. Colons forbidden inside branch/action/value. Value must match [A-Za-z0-9._-].
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from .constants import CUSTOM_ID_MAX_LENGTH, CUSTOM_ID_PREFIX
from .errors import OakError

logger = logging.getLogger(__name__)

_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class ParsedInteraction:
    """A parsed custom_id."""

    branch: str
    action: str
    value: str  # empty string if no value segment


class InteractionRouter:
    """Global router: maps custom_ids back to branches."""

    def parse(self, custom_id: str) -> ParsedInteraction | None:
        """Parse an oak-namespaced custom_id. Returns None if not an oak id."""
        if not custom_id.startswith(f"{CUSTOM_ID_PREFIX}:"):
            return None

        parts = custom_id.split(":")
        # oak:branch:action or oak:branch:action:value
        if len(parts) < 3:
            return None

        branch = parts[1]
        action = parts[2]
        value = parts[3] if len(parts) >= 4 else ""

        return ParsedInteraction(branch=branch, action=action, value=value)


class BranchInteractionHandle:
    """Scoped interaction handle for a single branch."""

    def __init__(self, router: InteractionRouter, branch_id: str):
        self._router = router
        self._branch_id = branch_id

    def custom_id(self, action: str, value: str = "") -> str:
        """
        Build a namespaced custom_id.

        Args:
            action: The action identifier (no colons).
            value: Optional state value (must match [A-Za-z0-9._-]).

        Returns:
            A custom_id string like "oak:branch:action" or "oak:branch:action:value".

        Raises:
            OakError: If validation fails.
        """
        if ":" in action:
            raise OakError(f"Action must not contain ':' — got '{action}'")

        if value:
            if ":" in value:
                raise OakError(f"Value must not contain ':' — got '{value}'")
            if not _VALUE_PATTERN.match(value):
                raise OakError(
                    f"Value must match [A-Za-z0-9._-] — got '{value}'"
                )
            cid = f"{CUSTOM_ID_PREFIX}:{self._branch_id}:{action}:{value}"
        else:
            cid = f"{CUSTOM_ID_PREFIX}:{self._branch_id}:{action}"

        if len(cid) > CUSTOM_ID_MAX_LENGTH:
            raise OakError(
                f"custom_id is {len(cid)} chars (max {CUSTOM_ID_MAX_LENGTH}). "
                f"Use DB-backed state instead of embedding large values."
            )

        return cid

    def parse(self, custom_id: str) -> ParsedInteraction | None:
        """Parse a custom_id, returning None if it doesn't belong to this branch."""
        result = self._router.parse(custom_id)
        if result and result.branch == self._branch_id:
            return result
        return None
