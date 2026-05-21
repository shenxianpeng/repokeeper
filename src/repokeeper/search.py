"""Codebase search for the Implementation Agent.

Provides grep-like and semantic file discovery so the native agent backend
can find relevant code beyond what smart file selection captures.  When
the LLM's initial file list is incomplete, these functions help the agent
explore the codebase programmatically.

Use :func:`search_codebase` for pattern matching and
:func:`discover_related_files` for structure-aware discovery.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from repokeeper.repo_context import SOURCE_EXTENSIONS, classify_file


def search_codebase(
    pattern: str,
    file_patterns: list[str] | None = None,
    max_results: int = 50,
    max_file_size_kb: int = 200,
    repo_root: Path = Path("."),
) -> list[dict[str, Any]]:
    """Search the codebase for a regex pattern using ``git grep``.

    Falls back to a slower Python-based scan when the repo is not a git
    worktree.

    Args:
        pattern: Regex pattern to search for.  Use Python regex syntax
                 with ``re.IGNORECASE`` applied automatically for simple
                 word searches (no regex metacharacters).
        file_patterns: Optional list of globs like ``["*.py", "*.js"]``
                       to limit search scope.  Defaults to source extensions.
        max_results: Maximum number of matches to return.
        max_file_size_kb: Skip files larger than this.
        repo_root: Repository root path.

    Returns:
        List of match dicts with ``file``, ``line_number``, ``line``,
        and ``context_before`` / ``context_after`` (3 lines each).
    """
    root = repo_root.resolve()

    # Detect if this is a bare regex or a simple keyword
    use_ignore_case = _is_keyword(pattern)

    # Build git grep args
    if file_patterns is None:
        file_patterns = [f"*{ext}" for ext in sorted(SOURCE_EXTENSIONS)]

    # Try git grep first (fastest)
    try:
        args = [
            "git", "grep", "-n", "-I", "--break",
            "--heading",
            f"--max-depth={max_results}",
        ]
        if use_ignore_case:
            args.append("-i")
        args.append("-E")
        args.append(pattern)
        args.append("--")
        args.extend(file_patterns)

        result = subprocess.run(
            args,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0 and result.stdout.strip():
            parsed = _parse_git_grep_output(
                result.stdout, max_results, repo_root=root,
            )
            if parsed:
                return parsed
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # Fallback: Python-based scan
    return _python_grep(
        pattern, file_patterns, max_results, max_file_size_kb,
        repo_root=root, ignore_case=use_ignore_case,
    )


def _is_keyword(pattern: str) -> bool:
    """Return True if the pattern looks like a plain keyword, not a regex."""
    # If there are regex metacharacters, don't force ignore-case.
    regex_meta = r".^$*+?{}[]\|()"
    return not any(ch in pattern for ch in regex_meta)


def _parse_git_grep_output(
    output: str,
    max_results: int,
    repo_root: Path,
) -> list[dict[str, Any]]:
    """Parse ``git grep`` output into structured match dicts."""
    matches: list[dict[str, Any]] = []
    current_file = ""

    for line in output.splitlines():
        if not line.strip():
            continue

        # git grep --heading puts file names on their own line without a colon
        if ":" not in line or (":" in line and line.startswith("/")):
            # File heading line
            current_file = line.strip()
            continue

        if ":" not in line:
            continue

        # "file:line_number:content"
        parts = line.split(":", 2)
        if len(parts) < 3:
            if current_file:
                # "line_number:content" after heading
                parts2 = line.split(":", 1)
                if len(parts2) == 2:
                    try:
                        lineno = int(parts2[0])
                        matches.append({
                            "file": current_file,
                            "line_number": lineno,
                            "line": parts2[1].strip()[:200],
                            "context_before": [],
                            "context_after": [],
                        })
                    except ValueError:
                        pass
            continue

        filepath = parts[0]
        try:
            lineno = int(parts[1])
        except ValueError:
            # Could be heading-format: parts[0] is the line number,
            # parts[1] + ":" + parts[2] is the content.
            if current_file:
                try:
                    lineno = int(parts[0])
                    content = parts[1]
                    if len(parts) > 2:
                        content += ":" + parts[2]
                    matches.append({
                        "file": current_file,
                        "line_number": lineno,
                        "line": content.strip()[:200],
                        "context_before": [],
                        "context_after": [],
                    })
                except (ValueError, IndexError):
                    pass
            continue

        matches.append({
            "file": filepath,
            "line_number": lineno,
            "line": parts[2].strip()[:200],
            "context_before": [],
            "context_after": [],
        })
        if len(matches) >= max_results:
            break

    return matches


def _python_grep(
    pattern: str,
    file_patterns: list[str],
    max_results: int,
    max_file_size_kb: int,
    repo_root: Path,
    ignore_case: bool,
) -> list[dict[str, Any]]:
    """Python-based fallback grep for non-git repositories."""
    matches: list[dict[str, Any]] = []

    # Build a regex
    flags = re.IGNORECASE if ignore_case else 0
    try:
        compiled = re.compile(pattern, flags)
    except re.error:
        compiled = re.compile(re.escape(pattern), flags)

    # Walk files
    for p in sorted(repo_root.rglob("*")):
        if not p.is_file():
            continue
        if len(matches) >= max_results:
            break

        # Filter by glob patterns
        if not any(p.match(pat) or p.name.endswith(pat.lstrip("*"))
                   for pat in file_patterns):
            continue

        try:
            size_kb = p.stat().st_size / 1024
            if size_kb > max_file_size_kb:
                continue
        except OSError:
            continue

        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue

        for i, line in enumerate(lines):
            if compiled.search(line):
                matches.append({
                    "file": str(p.relative_to(repo_root)),
                    "line_number": i + 1,
                    "line": line.strip()[:200],
                    "context_before": [],
                    "context_after": [],
                })
                if len(matches) >= max_results:
                    break

    return matches


def discover_related_files(
    seed_files: list[str],
    repo_root: Path = Path("."),
    max_files: int = 30,
) -> list[str]:
    """Find files related to a set of seed paths through imports and naming.

    Uses AST-based import analysis for Python and simple regex for other
    languages.  Returns a ranked list of related paths that the agent
    should consider reading.

    Args:
        seed_files: Repository-relative paths of files already identified
                    as relevant.
        repo_root: Repository root.
        max_files: Maximum number of related files to return.

    Returns:
        List of repository-relative file paths, ranked by relevance.
    """
    from repokeeper.repo_context import (
        SOURCE_EXTENSIONS,
        extract_local_dependencies,
        related_source_paths,
        related_test_paths,
    )

    root = repo_root.resolve()
    all_paths: set[str] = set()

    # Build a complete set of all source paths in the repo
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in SOURCE_EXTENSIONS:
            try:
                rel = str(p.relative_to(root))
                all_paths.add(rel)
            except ValueError:
                pass

    # Collect files reachable from seeds
    visited: set[str] = set(seed_files)
    queue: list[str] = list(seed_files)

    for path in queue:
        if len(visited) >= max_files:
            break

        # Find related tests
        for related in related_test_paths(path, all_paths):
            if related not in visited:
                visited.add(related)
                queue.append(related)

        # Find related sources
        for related in related_source_paths(path, all_paths):
            if related not in visited:
                visited.add(related)
                queue.append(related)

        # Find local imports
        try:
            content = (root / path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for dep in extract_local_dependencies(path, content, all_paths):
            if dep not in visited:
                visited.add(dep)
                queue.append(dep)

    return sorted(visited - set(seed_files))[:max_files]


def summarize_search_results(
    matches: list[dict[str, Any]],
    pattern: str,
) -> str:
    """Build a compact markdown summary of search results for the LLM prompt.

    Args:
        matches: Results from :func:`search_codebase`.
        pattern: The search pattern (for the heading).

    Returns:
        Markdown string.
    """
    if not matches:
        return f"`grep \"{pattern}\"` — no matches."

    # Group by file
    by_file: dict[str, list[dict[str, Any]]] = {}
    for m in matches:
        by_file.setdefault(m["file"], []).append(m)

    lines = [f"`grep \"{pattern}\"` — {len(matches)} match(es) in {len(by_file)} file(s):", ""]

    for filepath, file_matches in sorted(by_file.items()):
        kind = classify_file(filepath)
        lines.append(f"**{filepath}** ({kind})")
        for m in file_matches[:10]:  # max 10 matches per file
            lines.append(f"  L{m['line_number']}: `{m['line'][:120]}`")
        if len(file_matches) > 10:
            lines.append(f"  ... and {len(file_matches) - 10} more matches")
        lines.append("")

    return "\n".join(lines)
