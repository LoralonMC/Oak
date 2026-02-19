"""Oak framework exceptions."""


class OakError(Exception):
    """Base exception for all Oak framework errors."""


class BranchLoadError(OakError):
    """Raised when a branch fails to load."""

    def __init__(self, branch_id: str, reason: str):
        self.branch_id = branch_id
        self.reason = reason
        super().__init__(f"Failed to load branch '{branch_id}': {reason}")


class BranchNotFoundError(OakError):
    """Raised when a branch cannot be found."""

    def __init__(self, branch_id: str):
        self.branch_id = branch_id
        super().__init__(f"Branch not found: '{branch_id}'")


class ManifestError(OakError):
    """Raised when a branch.yml manifest is invalid."""

    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"Invalid manifest at '{path}': {reason}")


class ConfigError(OakError):
    """Raised when configuration is invalid."""
