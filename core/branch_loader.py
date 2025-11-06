"""
Branch loader for Oak - modular Discord bot framework.

Manages discovery, loading, and hot-reloading of branches (modular extensions).
Inspired by Minecraft Paper plugins and VSCode extensions.
"""

import os
import yaml
import logging
import importlib
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BranchMetadata:
    """Metadata about a branch."""
    name: str
    path: Path
    has_config: bool
    enabled: bool
    version: str = "1.0.0"


class BranchLoader:
    """Manages loading branches with auto-generated configs."""

    def __init__(self, branches_dir: str = "branches"):
        self.branches_dir = Path(branches_dir)
        self.loaded_branches: Dict[str, BranchMetadata] = {}

    def discover_branches(self) -> list[str]:
        """
        Discover all branches (both folder-based and single-file).

        Returns list of branch names.
        """
        branch_names = []

        for item in self.branches_dir.iterdir():
            # Skip private files/folders
            if item.name.startswith("_") or item.name.startswith("."):
                continue

            # Folder-based branch
            if item.is_dir():
                branch_file = item / "branch.py"
                if branch_file.exists() or (item / "__init__.py").exists():
                    branch_names.append(item.name)
                    logger.debug(f"Discovered branch: {item.name}")

            # Single-file branch (backwards compatible)
            elif item.suffix == ".py":
                branch_name = item.stem
                branch_names.append(branch_name)
                logger.debug(f"Discovered file branch: {branch_name}")

        return sorted(branch_names)

    def get_branch_path(self, branch_name: str) -> Optional[Path]:
        """Get the path for a branch."""
        # Check for folder-based branch first
        branch_folder = self.branches_dir / branch_name
        if branch_folder.is_dir():
            return branch_folder

        # Check for single-file branch
        branch_file = self.branches_dir / f"{branch_name}.py"
        if branch_file.exists():
            return branch_file

        return None

    def get_config_path(self, branch_name: str) -> Optional[Path]:
        """Get the config path for a branch."""
        branch_path = self.get_branch_path(branch_name)
        if not branch_path:
            return None

        if branch_path.is_dir():
            # Folder-based branch
            return branch_path / "config.yml"
        else:
            # Single-file branch - config in same directory with .yaml extension
            return branch_path.parent / f"{branch_name}.yaml"

    def load_config(self, branch_name: str) -> Dict[str, Any]:
        """Load config for a branch, generating default if it doesn't exist."""
        config_path = self.get_config_path(branch_name)

        if not config_path or not config_path.exists():
            # Generate default config
            default_config = self.get_default_config(branch_name)
            if config_path:
                self.save_config(branch_name, default_config)
            return default_config

        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f) or {}
            logger.info(f"Loaded config for {branch_name}")
            return config
        except Exception as e:
            logger.error(f"Failed to load config for {branch_name}: {e}")
            return self.get_default_config(branch_name)

    def save_config(self, branch_name: str, config: Dict[str, Any]):
        """Save config for a branch."""
        config_path = self.get_config_path(branch_name)
        if not config_path:
            logger.error(f"Cannot save config for {branch_name}: no valid path")
            return

        try:
            # Ensure directory exists
            config_path.parent.mkdir(parents=True, exist_ok=True)

            with open(config_path, "w") as f:
                yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

            logger.info(f"✅ Saved config for {branch_name}")
        except Exception as e:
            logger.error(f"Failed to save config for {branch_name}: {e}")

    def get_default_config(self, branch_name: str) -> Dict[str, Any]:
        """
        Get default config for a branch.

        Tries to load from branch's DEFAULT_CONFIG attribute or returns generic defaults.
        """
        # Try to import branch and get its default config
        try:
            # Try folder-based branch first
            branch_path = self.get_branch_path(branch_name)
            if branch_path and branch_path.is_dir():
                module = importlib.import_module(f"branches.{branch_name}.branch")
            else:
                module = importlib.import_module(f"branches.{branch_name}")

            if hasattr(module, "DEFAULT_CONFIG"):
                logger.info(f"Using branch-defined defaults for {branch_name}")
                return module.DEFAULT_CONFIG

            # Try to get from branch class
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and hasattr(attr, "DEFAULT_CONFIG"):
                    logger.info(f"Using class-defined defaults for {branch_name}")
                    return attr.DEFAULT_CONFIG

        except Exception as e:
            logger.debug(f"Could not load branch-defined defaults for {branch_name}: {e}")

        # Return generic default config
        return {
            "enabled": True,
            "version": "1.0.0",
            "settings": {
                # Generic defaults
                "example_setting": "value"
            }
        }

    def is_enabled(self, branch_name: str) -> bool:
        """Check if a branch is enabled in its config."""
        config = self.load_config(branch_name)
        return config.get("enabled", True)

    def reload_config(self, branch_name: str) -> Dict[str, Any]:
        """Reload config for a branch."""
        logger.info(f"Reloading config for {branch_name}")
        return self.load_config(branch_name)

    def get_load_path(self, branch_name: str) -> Optional[str]:
        """
        Get the import path for loading a branch.

        Returns:
            - "branches.branch_name" for folder-based branches (uses __init__.py)
            - "branches.branch_name" for single-file branches
        """
        branch_path = self.get_branch_path(branch_name)
        if not branch_path:
            return None

        # Always use branches.branch_name to load through __init__.py
        # This is the proper Python package structure
        return f"branches.{branch_name}"

    def list_branches(self) -> list[BranchMetadata]:
        """List all discovered branches with their metadata."""
        branches = []

        for branch_name in self.discover_branches():
            config = self.load_config(branch_name)
            branch_path = self.get_branch_path(branch_name)
            config_path = self.get_config_path(branch_name)

            branches.append(BranchMetadata(
                name=branch_name,
                path=branch_path,
                has_config=config_path.exists() if config_path else False,
                enabled=config.get("enabled", True),
                version=config.get("version", "1.0.0")
            ))

        return branches


# Global loader instance
_loader: Optional[BranchLoader] = None


def get_branch_loader() -> BranchLoader:
    """Get the global branch loader instance."""
    global _loader
    if _loader is None:
        _loader = BranchLoader()
    return _loader
