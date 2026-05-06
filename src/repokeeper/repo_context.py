"""Repository context collection for the Implementation Agent.

Walks the repository file tree, filters source files, and builds
a markdown context string for the LLM prompt.

Supports two collection strategies:

- **Direct** (:func:`collect_repo_files`): walks the repo and returns
  the best candidates, prioritized by config/docs → source code.
- **Two-step** (:func:`list_repo_files` + :func:`collect_specific_files`):
  first lists available files, lets the LLM pick what to read, then
  collects only the selected files.
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

# Directories with high signal (scanned earlier in two-step listing)
PRIORITY_DIRS: set[str] = {"src", "lib", "app", "pkg", "tests"}

MAX_FILE_SIZE: int = 40_000
MAX_FILES: int = 60

# Rough estimate: 1 token ≈ 4 characters for English text / code
CHARS_PER_TOKEN: int = 4


def _is_source_file(p: Path) -> bool:
    """Check if a path is a collectable source file."""
    return (
        p.is_file()
        and p.suffix in SOURCE_EXTENSIONS
        and not any(part in SKIP_DIRS for part in p.parts)
    )


def _read_file(p: Path) -> str | None:
    """Read a file's content, returning None if it's too large or unreadable."""
    try:
        content = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if len(content) > MAX_FILE_SIZE:
        return None
    return content


# ── Strategy 1: Direct collection (current, improved) ──────────────────────


def _file_priority_score(path: str) -> int:
    """Score a file for collection priority. Higher = more likely to keep.

    Prioritizes:
      1. Config/metadata files (always needed for context)
      2. Files in standard source directories (src/, lib/, etc.)
      3. Small files over large ones (more context per token)

    Returns a score where higher means higher priority.
    """
    score = 0
    lower = path.lower()

    # Config files: top priority
    if lower.endswith((".yml", ".yaml", ".toml", ".cfg", ".ini")):
        score += 200
    if "readme" in lower or "makefile" in lower or "dockerfile" in lower:
        score += 200
    if lower.endswith("pyproject.toml") or lower.endswith("package.json"):
        score += 300

    # Tests: important for understanding intent, but slightly lower than src
    if "test" in lower:
        score += 50

    # Standard source dirs bump
    if any(f"/{d}/" in f"/{path}" for d in PRIORITY_DIRS):
        score += 100

    # Docs are useful but low priority for implementation
    if "docs" in lower or "doc" in lower:
        score -= 50

    return score


def collect_repo_files(
    max_files: int = MAX_FILES,
    target_tokens: int | None = None,
) -> dict[str, str]:
    """Walk the repo and return ``{path: content}`` for source files.

    Files are collected, scored, and the top N are returned.  When
    ``target_tokens`` is set, the function stops adding files once the
    estimated token count exceeds the target (up to ``max_files``).

    Args:
        max_files: Maximum number of files to include.
        target_tokens: Optional token budget for context.  When set,
            files are added in priority order until the budget is
            exhausted or ``max_files`` is reached.

    Returns:
        Dict mapping file paths to their contents.
    """
    # Collect all eligible files with content
    candidates: list[tuple[str, str, int]] = []  # (path, content, score)
    for p in sorted(Path(".").rglob("*")):
        if not _is_source_file(p):
            continue
        content = _read_file(p)
        if content is None:
            continue
        path = str(p)
        candidates.append((path, content, _file_priority_score(path)))

    # Sort by score descending, then by path for stability
    candidates.sort(key=lambda x: (-x[2], x[0]))

    # Select top files, respecting token budget if set
    selected: dict[str, str] = {}
    for path, content, _score in candidates:
        if len(selected) >= max_files:
            break
        if target_tokens is not None:
            estimated = estimate_tokens(selected | {path: content})
            if estimated > target_tokens:
                continue  # skip this file, try smaller ones
        selected[path] = content

    return selected


# ── Strategy 2: Two-step smart selection ────────────────────────────────────


def list_repo_files() -> list[dict[str, object]]:
    """List all source files with metadata (no content).

    Used as input to the first LLM call in the two-step flow, so the
    LLM can select which files to read.

    Returns:
        List of dicts with ``path``, ``size``, and ``suffix`` keys.
    """
    entries: list[dict[str, object]] = []
    for p in sorted(Path(".").rglob("*")):
        if not _is_source_file(p):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        if size > MAX_FILE_SIZE:
            continue
        entries.append({
            "path": str(p),
            "size": size,
            "suffix": p.suffix,
        })

    # Sort: priority dirs first, then by path
    def _sort_key(e: dict[str, object]) -> tuple[int, str]:
        p = str(e["path"])
        prio = 0
        if any(f"/{d}/" in f"/{p}" for d in PRIORITY_DIRS):
            prio = -1
        return (prio, p)

    entries.sort(key=_sort_key)
    return entries


def collect_specific_files(paths: list[str]) -> dict[str, str]:
    """Read content for specific file paths.

    Paths are validated (no traversal, no blocked prefixes).  Files
    that don't exist, are too large, or are unreadable are silently
    skipped.

    Args:
        paths: Repository-relative file paths.

    Returns:
        Dict mapping path to content.
    """
    from repokeeper.git_ops import safe_repo_path

    files: dict[str, str] = {}
    for filepath in paths:
        try:
            p = safe_repo_path(filepath)
        except ValueError:
            continue
        content = _read_file(p)
        if content is not None:
            files[filepath] = content
    return files


def estimate_tokens(files: dict[str, str]) -> int:
    """Roughly estimate the token count for a set of files in context format.

    Uses a simple character-based heuristic (1 token ≈ 4 chars).  This
    is a loose upper-bound estimate; real tokenizers vary by model.

    Args:
        files: Dict mapping file paths to their contents.

    Returns:
        Estimated token count.
    """
    # Context format adds ~30 chars overhead per file (### path\n```\n...```)
    total = 0
    for path, content in files.items():
        total += len(path) + len(content) + 30
    return total // CHARS_PER_TOKEN


# ── Context string builder ─────────────────────────────────────────────────


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
