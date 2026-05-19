"""
BranchLoader: discover, validate, load, reload, and unload branches.

All public API uses branch_id (from manifest id field).
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from .config import load_branch_config
from .constants import BRANCH_MANIFEST_FILE
from .context import BranchContext
from .database import BranchDatabase
from .errors import BranchLoadError, BranchNotFoundError
from .events import BranchEventHandle, EventBus
from .interactions import BranchInteractionHandle, InteractionRouter
from .manifest import BranchManifest
from . import __version__ as OAK_VERSION

if TYPE_CHECKING:
    from .bot import OakBot
    from .branch import OakBranch

logger = logging.getLogger(__name__)


def _parse_version(s: str) -> tuple[int, ...]:
    """Parse a dotted version string into a tuple of ints."""
    return tuple(int(x) for x in s.split("."))


class BranchLoader:
    """Discovers, loads, and manages Oak branches."""

    def __init__(self, bot: "OakBot", branches_dir: Path, event_bus: EventBus, router: InteractionRouter):
        self.bot = bot
        self.branches_dir = branches_dir
        self.event_bus = event_bus
        self.router = router

        # branch_id -> manifest
        self._manifests: dict[str, BranchManifest] = {}
        # branch_id -> folder Path
        self._paths: dict[str, Path] = {}
        # branch_id -> loaded OakBranch instance
        self._loaded: dict[str, "OakBranch"] = {}
        # Branches skipped (disabled) during load_all
        self._skipped: set[str] = set()
        # Branches that failed to load: {branch_id: error_message}
        self._load_failures: dict[str, str] = {}

    # -- Discovery --

    def discover(self) -> dict[str, BranchManifest]:
        """Scan branches/ for branch.yml files. Returns {id: manifest}."""
        self._manifests.clear()
        self._paths.clear()
        self._skipped.clear()
        self._load_failures.clear()

        if not self.branches_dir.exists():
            return {}

        for item in sorted(self.branches_dir.iterdir()):
            if not item.is_dir() or item.name.startswith(("_", ".")):
                continue

            manifest_path = item / BRANCH_MANIFEST_FILE
            if not manifest_path.exists():
                continue

            try:
                manifest = BranchManifest.from_file(manifest_path)
                if manifest.id in self._manifests:
                    existing_path = self._paths[manifest.id]
                    logger.error(
                        "Duplicate branch id '%s' in %s and %s; skipping %s",
                        manifest.id,
                        existing_path,
                        item,
                        item,
                    )
                    continue
                self._manifests[manifest.id] = manifest
                self._paths[manifest.id] = item
            except Exception as e:
                logger.error(f"Bad manifest in {item.name}/: {e}")

        logger.info(f"Discovered {len(self._manifests)} branches")
        return dict(self._manifests)

    # -- Dependency resolution --

    def resolve_load_order(self) -> list[str]:
        """Topological sort by dependencies, then by priority."""
        # Build adjacency: branch -> set of branches it depends on
        graph: dict[str, set[str]] = {}
        broken: set[str] = set()

        for bid, manifest in self._manifests.items():
            graph[bid] = set()
            for dep in manifest.dependencies:
                if dep in self._manifests:
                    graph[bid].add(dep)
                else:
                    logger.error(
                        f"Branch '{bid}' depends on '{dep}' which was not discovered; "
                        f"removing '{bid}' from load order"
                    )
                    broken.add(bid)

        # Remove broken branches from the graph before sorting
        for bid in broken:
            del graph[bid]

        # Simpler: just do a stable topological sort
        order: list[str] = []
        visited: set[str] = set()
        visiting: set[str] = set()
        cycle_members: set[str] = set()

        def visit(bid: str) -> bool:
            """Visit a node; returns False if it's part of a cycle."""
            if bid in visited:
                return bid not in cycle_members
            if bid in visiting:
                logger.error(f"Circular dependency detected involving '{bid}'")
                cycle_members.add(bid)
                return False
            visiting.add(bid)
            for dep in graph.get(bid, set()):
                if not visit(dep):
                    # Dependency is in a cycle — this branch can't load either
                    cycle_members.add(bid)
            visiting.discard(bid)
            visited.add(bid)
            if bid not in cycle_members:
                order.append(bid)
            return bid not in cycle_members

        # Visit in priority order (lower priority number = loaded first)
        for bid in sorted(graph, key=lambda b: self._manifests[b].priority):
            visit(bid)

        # Record cycle members as load failures so they're visible in /health
        for bid in cycle_members:
            self._load_failures[bid] = "Circular dependency detected"

        return order

    # -- Loading --

    async def load_branch(self, branch_id: str) -> "OakBranch":
        """
        Load a single branch by its manifest id.

        Validates manifest, imports module, verifies class, builds context, instantiates, adds cog.
        """
        if branch_id in self._loaded:
            raise BranchLoadError(branch_id, "Already loaded")

        manifest = self._manifests.get(branch_id)
        if not manifest:
            raise BranchNotFoundError(branch_id)

        branch_dir = self._paths[branch_id]

        # Check oak_version requirement
        if manifest.oak_version:
            try:
                required = _parse_version(manifest.oak_version)
                current = _parse_version(OAK_VERSION)
                if current < required:
                    msg = (
                        f"Requires Oak >= {manifest.oak_version}, "
                        f"current is {OAK_VERSION}"
                    )
                    self._load_failures[branch_id] = msg
                    raise BranchLoadError(branch_id, msg)
            except ValueError:
                logger.warning(
                    f"Branch '{branch_id}' has unparseable oak_version: "
                    f"'{manifest.oak_version}', skipping version check"
                )

        # Check hard dependencies are loaded
        for dep in manifest.dependencies:
            if dep not in self._loaded:
                raise BranchLoadError(
                    branch_id, f"Missing dependency: '{dep}' (not loaded)"
                )

        # Import the module
        module_parts = manifest.main.rsplit(".", 1)
        if len(module_parts) != 2:
            raise BranchLoadError(
                branch_id,
                f"'main' must be 'module.ClassName', got '{manifest.main}'",
            )

        module_name, class_name = module_parts
        import_path = f"branches.{branch_dir.name}.{module_name}"

        try:
            # Clear cached modules so reload picks up changes
            prefix = f"branches.{branch_dir.name}"
            stale = [key for key in sys.modules if key == prefix or key.startswith(f"{prefix}.")]
            for key in stale:
                del sys.modules[key]
            module = importlib.import_module(import_path)
        except Exception as e:
            raise BranchLoadError(branch_id, f"Import error: {e}") from e

        # Get the class
        branch_cls = getattr(module, class_name, None)
        if branch_cls is None:
            raise BranchLoadError(
                branch_id, f"Class '{class_name}' not found in {import_path}"
            )

        from .branch import OakBranch

        if not (isinstance(branch_cls, type) and issubclass(branch_cls, OakBranch)):
            raise BranchLoadError(
                branch_id,
                f"'{class_name}' must be a subclass of OakBranch",
            )

        # Read DEFAULT_CONFIG from module, default to {}
        default_config = getattr(module, "DEFAULT_CONFIG", getattr(branch_cls, "DEFAULT_CONFIG", {}))

        # Merge config
        config = load_branch_config(branch_dir, default_config, branch_id)

        # Build database (if requested)
        db = None
        if manifest.database:
            metrics = getattr(self.bot, "metrics", None)
            db = BranchDatabase(branch_dir / "data.db", branch_id=branch_id, metrics=metrics)

        # Build context
        branch_logger = logging.getLogger(f"oak.branch.{branch_id}")
        event_handle = BranchEventHandle(self.event_bus, branch_id)
        interaction_handle = BranchInteractionHandle(self.router, branch_id)

        ctx = BranchContext(
            bot=self.bot,
            id=branch_id,
            name=manifest.name,
            config=config,
            db=db,
            logger=branch_logger,
            data_dir=branch_dir,
            events=event_handle,
            interactions=interaction_handle,
        )

        # Instantiate
        try:
            instance = branch_cls(ctx)
        except Exception as e:
            raise BranchLoadError(branch_id, f"Instantiation failed: {e}") from e

        # Add as cog (triggers cog_load -> on_enable). If on_enable fails
        # partway, it may have registered persistent views via bot.add_view()
        # or scheduled tasks via register_task() that discord.py's add_cog
        # rollback won't touch. Clean those up explicitly.
        try:
            await self.bot.add_cog(instance)
        except Exception as e:
            # Best-effort: try to remove the cog in case it was partially added
            try:
                await self.bot.remove_cog(instance.qualified_name)
            except Exception:
                pass
            # Unregister any tasks the branch had time to schedule
            try:
                self.bot.task_registry.unregister_all(branch_id)
            except Exception:
                logger.exception(f"Failed to unregister tasks for failed branch '{branch_id}'")
            await self._cleanup_branch_resources(branch_id, event_handle, db)
            raise BranchLoadError(branch_id, f"add_cog/on_enable failed: {e}") from e

        self._loaded[branch_id] = instance
        # Clear any stale skip/failure state so /health and /branches reflect reality
        self._skipped.discard(branch_id)
        self._load_failures.pop(branch_id, None)
        logger.info(f"Loaded branch: {manifest.name} v{manifest.version}")

        return instance

    async def _cleanup_branch_resources(
        self,
        branch_id: str,
        event_handle: BranchEventHandle | None,
        db: BranchDatabase | None,
    ) -> None:
        """Clean up event subscriptions and database for a branch."""
        if event_handle:
            try:
                event_handle.cleanup()
            except Exception:
                logger.exception(f"Failed to clean up events for branch '{branch_id}'")
        if db:
            try:
                await db.close()
            except Exception:
                logger.exception(f"Failed to close database for branch '{branch_id}'")

    async def unload_branch(self, branch_id: str) -> None:
        """Unload a branch: remove cog, clean up events/interactions, close DB."""
        instance = self._loaded.get(branch_id)
        if not instance:
            raise BranchNotFoundError(branch_id)

        # Remove cog (triggers cog_unload -> on_disable)
        await self.bot.remove_cog(instance.qualified_name)

        # Unregister scheduled tasks
        self.bot.task_registry.unregister_all(branch_id)

        # Cleanup event subscriptions and database
        await self._cleanup_branch_resources(branch_id, instance.events, instance.db)

        del self._loaded[branch_id]
        logger.info(f"Unloaded branch: {branch_id}")

    async def reload_branch(self, branch_id: str) -> "OakBranch":
        """Unload then load a branch. Config is re-read from disk."""
        await self.unload_branch(branch_id)

        # Re-discover this branch's manifest (picks up manifest changes)
        # Always key under original branch_id to avoid stale entries if id changed
        branch_dir = self._paths.get(branch_id)
        if branch_dir:
            manifest_path = branch_dir / BRANCH_MANIFEST_FILE
            if manifest_path.exists():
                manifest = BranchManifest.from_file(manifest_path)
                self._manifests[branch_id] = manifest
                self._paths[branch_id] = branch_dir

        try:
            instance = await self.load_branch(branch_id)
        except Exception as e:
            # Record the failure so /branches and /health show the broken state
            # instead of treating the branch as cleanly absent.
            self._load_failures[branch_id] = str(e)
            logger.error(
                f"Reload failed for branch '{branch_id}'; branch is now unloaded"
            )
            raise

        # If bot is already ready, fire on_ready immediately for the reloaded branch
        if getattr(self.bot, "_ready_fired", False):
            try:
                await instance.on_ready()
            except Exception:
                logger.exception(f"on_ready() failed for reloaded branch '{branch_id}'")

        return instance

    def _is_branch_enabled(self, branch_dir: Path) -> bool:
        """Check if a branch's config.yml has enabled: false."""
        config_path = branch_dir / "config.yml"
        if not config_path.exists():
            return True
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            return raw.get("enabled", True)
        except Exception as e:
            logger.warning(
                "Failed to parse %s while checking enabled flag: %s",
                config_path,
                e,
            )
            return True

    async def load_all(self) -> tuple[list[str], list[str], list[tuple[str, str]]]:
        """
        Discover all branches, resolve order, load each enabled branch.

        Returns (loaded, skipped, failed) lists.
        """
        self.discover()
        order = self.resolve_load_order()

        loaded: list[str] = []
        skipped: list[str] = []
        failed: list[tuple[str, str]] = []

        for branch_id in order:
            branch_dir = self._paths[branch_id]

            if not self._is_branch_enabled(branch_dir):
                skipped.append(branch_id)
                self._skipped.add(branch_id)
                logger.info(f"Skipped {branch_id} (disabled)")
                continue

            try:
                await self.load_branch(branch_id)
                loaded.append(branch_id)
            except Exception as e:
                failed.append((branch_id, str(e)))
                self._load_failures[branch_id] = str(e)
                logger.error(f"Failed to load {branch_id}: {e}")

        return loaded, skipped, failed

    # -- Queries --

    @property
    def loaded_branches(self) -> dict[str, "OakBranch"]:
        return dict(self._loaded)

    @property
    def manifests(self) -> dict[str, BranchManifest]:
        return dict(self._manifests)

    def get_branch(self, branch_id: str) -> "OakBranch | None":
        return self._loaded.get(branch_id)

    def require_branch(self, branch_id: str) -> "OakBranch":
        """Get a loaded branch by id, raising BranchNotFoundError if not loaded."""
        instance = self._loaded.get(branch_id)
        if instance is None:
            raise BranchNotFoundError(branch_id)
        return instance

    def is_loaded(self, branch_id: str) -> bool:
        return branch_id in self._loaded

    @property
    def skipped_branches(self) -> set[str]:
        """Branch ids that were skipped (disabled) during load_all."""
        return set(self._skipped)

    @property
    def failed_branches(self) -> dict[str, str]:
        """Branch ids that failed to load, with error messages."""
        return dict(self._load_failures)

    def discovered_ids(self) -> list[str]:
        """All discovered branch ids (loaded or not)."""
        return list(self._manifests.keys())
