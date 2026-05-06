"""Structured exception hierarchy for RepoKeeper.

All RepoKeeper-specific exceptions inherit from :class:`RepoKeeperError`,
allowing callers to catch fine-grained error types without inspecting
exception message strings.
"""

from __future__ import annotations


class RepoKeeperError(Exception):
    """Base class for all RepoKeeper exceptions."""


class AuthError(RepoKeeperError):
    """Authentication or authorization failure.

    Raised when a GitHub token is missing, expired, or lacks required scopes.
    """


class ConfigError(RepoKeeperError):
    """Configuration error (missing or invalid profile / workflow)."""


class LLMError(RepoKeeperError):
    """LLM communication or response parsing error."""


class LLMParseError(LLMError):
    """Could not parse the LLM JSON response after all repair attempts."""


class VerificationError(RepoKeeperError):
    """Pre-push verification (linter / tests) failed."""


class PermissionDeniedError(RepoKeeperError):
    """GitHub refused an operation (push, PR creation) due to permissions."""


class GitOperationError(RepoKeeperError):
    """A git subprocess operation failed unexpectedly."""
