"""RepoKeeper — AI-powered open source maintainer agent."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("repokeeper")
except PackageNotFoundError:
    __version__ = "0.0.0"
