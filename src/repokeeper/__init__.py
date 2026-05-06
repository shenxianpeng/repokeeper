"""RepoKeeper — AI-powered open source maintainer agent."""

from importlib.metadata import PackageNotFoundError, version

from repokeeper.exceptions import (  # noqa: F401
    AuthError,
    ConfigError,
    GitOperationError,
    LLMError,
    LLMParseError,
    PermissionDeniedError,
    RepoKeeperError,
    VerificationError,
)

try:
    __version__ = version("repokeeper")
except PackageNotFoundError:
    __version__ = "0.0.0"
