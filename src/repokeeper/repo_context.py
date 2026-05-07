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

import ast
import re
from pathlib import Path
from typing import Any

# File extensions considered "source" for LLM context
SOURCE_EXTENSIONS: set[str] = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".rb", ".sh",
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

CONFIG_FILENAMES: set[str] = {
    "dockerfile",
    "makefile",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
    "tox.ini",
}


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


def classify_file(path: str) -> str:
    """Classify a repository path for context ranking and prompts."""
    lower = path.lower()
    name = Path(lower).name
    suffix = Path(lower).suffix
    parts = set(Path(lower).parts)

    if "test" in name or "tests" in parts or "__tests__" in parts:
        return "test"
    if name in CONFIG_FILENAMES or suffix in {".yml", ".yaml", ".toml", ".cfg", ".ini"}:
        return "config"
    if "readme" in name or suffix in {".md", ".rst", ".txt"} or "docs" in parts:
        return "docs"
    if suffix in SOURCE_EXTENSIONS:
        return "source"
    return "other"


def _module_to_paths(module: str) -> list[str]:
    """Return likely Python file paths for an import module name."""
    if not module:
        return []
    base = module.replace(".", "/")
    return [f"{base}.py", f"{base}/__init__.py", f"src/{base}.py", f"src/{base}/__init__.py"]


def _resolve_js_import(import_path: str, current_path: str, all_paths: set[str]) -> str | None:
    """Resolve a relative JS/TS import to a known repository path."""
    if not import_path.startswith("."):
        return None

    base = Path(current_path).parent / import_path
    candidates: list[str] = []
    for suffix in (".ts", ".tsx", ".js", ".jsx", ".json"):
        candidates.append(base.with_suffix(suffix).as_posix())
    for suffix in (".ts", ".tsx", ".js", ".jsx"):
        candidates.append((base / f"index{suffix}").as_posix())

    for candidate in candidates:
        if candidate in all_paths:
            return candidate
    return None


def extract_local_dependencies(
    path: str,
    content: str,
    all_paths: set[str] | None = None,
) -> list[str]:
    """Extract local dependency hints for a file.

    This intentionally uses cheap static heuristics.  The result is not a full
    build graph, but it is enough to keep adjacent modules in the LLM context.
    """
    all_paths = all_paths or set()
    deps: set[str] = set()
    suffix = Path(path).suffix

    if suffix == ".py":
        try:
            tree = ast.parse(content)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                module = ""
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module = alias.name
                        for candidate in _module_to_paths(module):
                            if candidate in all_paths:
                                deps.add(candidate)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        module = node.module
                        for candidate in _module_to_paths(module):
                            if candidate in all_paths:
                                deps.add(candidate)
                    if node.level and node.module:
                        base = Path(path).parent
                        for _ in range(max(node.level - 1, 0)):
                            base = base.parent
                        rel = (base / node.module.replace(".", "/")).as_posix()
                        for candidate in (f"{rel}.py", f"{rel}/__init__.py"):
                            if candidate in all_paths:
                                deps.add(candidate)

    if suffix in {".js", ".jsx", ".ts", ".tsx"}:
        for match in re.finditer(r"""from\s+['"]([^'"]+)['"]|import\(['"]([^'"]+)['"]\)""", content):
            import_path = match.group(1) or match.group(2) or ""
            resolved = _resolve_js_import(import_path, path, all_paths)
            if resolved:
                deps.add(resolved)

    deps.discard(path)
    return sorted(deps)


def related_test_paths(path: str, all_paths: set[str]) -> list[str]:
    """Find likely tests for a source file using naming conventions."""
    if classify_file(path) == "test":
        return []

    p = Path(path)
    stem = p.stem
    candidates: set[str] = set()

    candidates.update({
        f"tests/test_{stem}.py",
        f"tests/{stem}_test.py",
        f"test/test_{stem}.py",
        f"test/{stem}_test.py",
    })

    if p.parts and p.parts[0] in {"src", "lib", "app", "pkg"}:
        rel = Path(*p.parts[1:])
        candidates.add((Path("tests") / rel.parent / f"test_{rel.name}").as_posix())
        candidates.add((Path("tests") / rel).as_posix())

    matches = [candidate for candidate in candidates if candidate in all_paths]

    # Fallback: match by stem across any known test file.
    if not matches:
        for candidate in all_paths:
            if classify_file(candidate) != "test":
                continue
            candidate_stem = Path(candidate).stem
            if candidate_stem in {f"test_{stem}", f"{stem}_test"}:
                matches.append(candidate)

    return sorted(set(matches))


def related_source_paths(path: str, all_paths: set[str]) -> list[str]:
    """Find likely source files for a test file using naming conventions."""
    if classify_file(path) != "test":
        return []

    stem = Path(path).stem
    if stem.startswith("test_"):
        stem = stem.removeprefix("test_")
    elif stem.endswith("_test"):
        stem = stem.removesuffix("_test")

    matches = []
    for candidate in all_paths:
        if classify_file(candidate) != "source":
            continue
        if Path(candidate).stem == stem:
            matches.append(candidate)
    return sorted(matches)


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
    kind = classify_file(path)

    # Config files: top priority
    if kind == "config":
        score += 200
    if "readme" in lower or "makefile" in lower or "dockerfile" in lower:
        score += 200
    if lower.endswith("pyproject.toml") or lower.endswith("package.json"):
        score += 300

    # Tests: important for understanding intent, but slightly lower than src
    if kind == "test":
        score += 50

    # Standard source dirs bump
    if any(f"/{d}/" in f"/{path}" for d in PRIORITY_DIRS):
        score += 100

    # Docs are useful but low priority for implementation
    if kind == "docs":
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


def list_repo_files() -> list[dict[str, Any]]:
    """List all source files with metadata (no content).

    Used as input to the first LLM call in the two-step flow, so the
    LLM can select which files to read.

    Returns:
        List of dicts with ``path``, ``size``, and ``suffix`` keys.
    """
    candidates: list[tuple[str, int, str, str]] = []
    for p in sorted(Path(".").rglob("*")):
        if not _is_source_file(p):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        if size > MAX_FILE_SIZE:
            continue
        content = _read_file(p)
        if content is None:
            continue
        candidates.append((str(p), size, p.suffix, content))

    all_paths = {path for path, _size, _suffix, _content in candidates}

    entries: list[dict[str, Any]] = []
    for path, size, suffix, content in candidates:
        deps = extract_local_dependencies(path, content, all_paths)
        tests = related_test_paths(path, all_paths)
        sources = related_source_paths(path, all_paths)
        entries.append({
            "path": path,
            "size": size,
            "suffix": suffix,
            "kind": classify_file(path),
            "score": _file_priority_score(path),
            "local_dependencies": deps[:8],
            "related_tests": tests[:8],
            "related_sources": sources[:8],
        })

    # Sort: priority dirs first, then by path
    def _sort_key(e: dict[str, Any]) -> tuple[int, str]:
        p = str(e["path"])
        return (-int(e.get("score", 0)), p)

    entries.sort(key=_sort_key)
    return entries


def collect_specific_files(
    paths: list[str],
    max_files: int | None = None,
    target_tokens: int | None = None,
) -> dict[str, str]:
    """Read content for specific file paths.

    Paths are validated (no traversal, no blocked prefixes).  Files
    that don't exist, are too large, or are unreadable are silently
    skipped.

    Args:
        paths: Repository-relative file paths.
        max_files: Optional maximum number of files to read.
        target_tokens: Optional context token budget.

    Returns:
        Dict mapping path to content.
    """
    from repokeeper.git_ops import safe_repo_path

    files: dict[str, str] = {}
    for filepath in paths:
        if max_files is not None and len(files) >= max_files:
            break
        try:
            p = safe_repo_path(filepath)
        except ValueError:
            continue
        content = _read_file(p)
        if content is not None:
            if target_tokens is not None:
                estimated = estimate_tokens(files | {filepath: content})
                if estimated > target_tokens:
                    continue
            files[filepath] = content
    return files


def expand_context_paths(
    selected_paths: list[str],
    max_files: int = MAX_FILES,
    target_tokens: int | None = None,
) -> dict[str, str]:
    """Read selected files plus nearby tests and local dependencies."""
    if not selected_paths:
        return {}

    entries: dict[str, dict[str, Any]] = {str(entry["path"]): entry for entry in list_repo_files()}
    expanded: list[str] = []

    def add(path: str) -> None:
        if path in entries and path not in expanded:
            expanded.append(path)

    for path in selected_paths:
        add(path)
        entry = entries.get(path)
        if not entry:
            continue
        for key in ("related_tests", "related_sources", "local_dependencies"):
            for related in entry.get(key, []):
                if isinstance(related, str):
                    add(related)

    return collect_specific_files(expanded, max_files=max_files, target_tokens=target_tokens)


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


def compress_patch(patch: str, max_chars: int = 12_000) -> str:
    """Compress a unified diff while preserving changed lines and headers."""
    if len(patch) <= max_chars:
        return patch

    important: list[str] = []
    omitted = 0
    for line in patch.splitlines():
        if line.startswith(("diff --git ", "index ", "--- ", "+++ ", "@@", "+", "-")):
            important.append(line)
        else:
            omitted += 1

    compressed = "\n".join(important)
    header = f"[RepoKeeper compressed diff: omitted {omitted} unchanged context lines]\n"
    compressed = header + compressed

    if len(compressed) <= max_chars:
        return compressed

    head = compressed[: max_chars // 2]
    tail = compressed[-max_chars // 2:]
    return f"{head}\n\n[RepoKeeper compressed diff: middle truncated]\n\n{tail}"
