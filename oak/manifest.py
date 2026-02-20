"""
BranchManifest: parses and validates branch.yml files.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import ManifestError

logger = logging.getLogger(__name__)

_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class BranchManifest:
    """Parsed branch.yml manifest."""

    # Required
    id: str
    version: str
    main: str  # e.g. "branch.Link"

    # Optional
    name: str = ""  # display name, defaults to id
    description: str = ""
    author: str = ""
    dependencies: tuple[str, ...] = ()
    soft_dependencies: tuple[str, ...] = ()
    permissions: dict[str, Any] = field(default_factory=dict)
    database: bool = False
    priority: int = 100
    oak_version: str = ""

    def __post_init__(self):
        # default display name to id
        if not self.name:
            object.__setattr__(self, "name", self.id)

    @classmethod
    def from_file(cls, path: Path) -> BranchManifest:
        """Parse a branch.yml file into a BranchManifest."""
        if not path.exists():
            raise ManifestError(str(path), "File does not exist")

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ManifestError(str(path), f"Invalid YAML: {e}")

        if not isinstance(data, dict):
            raise ManifestError(str(path), "Manifest must be a YAML mapping")

        # Validate required fields
        for required in ("id", "version", "main"):
            if required not in data:
                raise ManifestError(str(path), f"Missing required field: '{required}'")

        branch_id = str(data["id"])
        if not branch_id.replace("_", "").replace("-", "").isalnum():
            raise ManifestError(
                str(path),
                f"id must be alphanumeric (with _ or -), got: '{branch_id}'",
            )

        # Reject IDs that start with a digit
        if branch_id[0].isdigit():
            raise ManifestError(
                str(path),
                f"id must not start with a digit, got: '{branch_id}'",
            )

        # Dependencies — warn on wrong type
        deps = data.get("dependencies", [])
        if not isinstance(deps, list):
            logger.warning(
                f"Manifest {path}: 'dependencies' should be a list, got {type(deps).__name__}; defaulting to []"
            )
            deps = []

        soft_deps = data.get("soft_dependencies", [])
        if not isinstance(soft_deps, list):
            logger.warning(
                f"Manifest {path}: 'soft_dependencies' should be a list, got {type(soft_deps).__name__}; defaulting to []"
            )
            soft_deps = []

        permissions = data.get("permissions", {})
        if not isinstance(permissions, dict):
            logger.warning(
                f"Manifest {path}: 'permissions' should be a dict, got {type(permissions).__name__}; defaulting to {{}}"
            )
            permissions = {}

        version = str(data["version"])
        if version and not _SEMVER_PATTERN.match(version):
            logger.warning(
                f"Manifest {path}: version '{version}' does not match X.Y.Z semver format"
            )

        return cls(
            id=branch_id,
            version=version,
            main=str(data["main"]),
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            author=str(data.get("author", "")),
            dependencies=tuple(str(d) for d in deps),
            soft_dependencies=tuple(str(d) for d in soft_deps),
            permissions=permissions,
            database=bool(data.get("database", False)),
            priority=int(data.get("priority", 100)),
            oak_version=str(data.get("oak_version", "")),
        )
