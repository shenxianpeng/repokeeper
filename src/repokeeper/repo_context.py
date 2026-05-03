"""Repository context collection for the Implementation Agent.

Walks the repository file tree, filters source files, and builds
a markdown context string for the LLM prompt.
"""

from __future__ import annotations

from pathlib import Path

# File extensions considered "source" for LLM context
SOURCE_EXTENSIONS: set[str] = {
    ".py", ".js", ".ts", ".go", ".java", ".rb", ".sh",
    ".yml", ".yaml", ".toml", ".cfg", ".ini", ".md", ".txt",
    ".json", ".rst",
}

# Directories that are always skipped during file collection
SKIP_DIRS: set[str] = {
    ".git", "node_modules", "__pycache__", ".tox",
    "venv", ".venv", "dist", "build", ".eggs", ".mypy_cache",
    ".pytest_cache", ".ruff_cache",
}

MAX_FILE_SIZE: int = 40_000
MAX_FILES: int = 40


def collect_repo_files(max_files: int = MAX_FILES) -> dict[str, str]:
    """Walk the repo and return ``{path: content}`` for source files.

    Args:
        max_files: Maximum number of files to include.

    Returns:
        Dict mapping file paths to their contents.
    """
    files: dict[str, str] = {}
    for p in sorted(Path(".").rglob("*")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if not p.is_file():
            continue
        if p.suffix not in SOURCE_EXTENSIONS:
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if len(content) > MAX_FILE_SIZE:
            continue
        files[str(p)] = content

    if len(files) > max_files:
        priority = {
            k: v for k, v in files.items()
            if "readme" in k.lower()
            or k.endswith((".yml", ".yaml", ".toml", ".cfg", ".ini"))
        }
        source = {k: v for k, v in files.items() if k not in priority}
        remaining = max_files - len(priority)
        files = {**priority, **dict(list(source.items())[:remaining])}

    return files


def build_context_string(files: dict[str, str]) -> str:
    """Build a markdown context string from collected files.

    Args:
        files: Dict mapping file paths to their contents.

    Returns:
        Markdown string with each file in its own code block.
    """
    parts = []
    for path, content in files.items():
        parts.append(f"### {path}\n```\n{content}\n```")
    return "\n\n".join(parts)
