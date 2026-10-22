"""Tests for repository context collection (repo_context.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from repokeeper import repo_context
from repokeeper.repo_context import (
    MAX_FILE_SIZE,
    _module_to_paths,
    _resolve_js_import,
    build_context_string,
    classify_file,
    collect_repo_files,
    collect_specific_files,
    compress_patch,
    estimate_tokens,
    expand_context_paths,
    extract_local_dependencies,
    list_repo_files,
    related_source_paths,
    related_test_paths,
)

# ── classify_file / _module_to_paths / _resolve_js_import ────────────────────


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_app.py", "test"),
        ("pyproject.toml", "config"),
        ("docs/guide.md", "docs"),
        ("src/app.py", "source"),
        ("assets/logo.png", "other"),
    ],
)
def test_classify_file(path, expected):
    assert classify_file(path) == expected


def test_module_to_paths():
    assert _module_to_paths("") == []
    assert _module_to_paths("pkg.mod") == [
        "pkg/mod.py",
        "pkg/mod/__init__.py",
        "src/pkg/mod.py",
        "src/pkg/mod/__init__.py",
    ]


def test_resolve_js_import_resolves_relative_paths_only():
    all_paths = {"src/utils/index.ts", "src/helpers.js"}

    assert _resolve_js_import("react", "src/app.tsx", all_paths) is None
    assert _resolve_js_import("./helpers", "src/app.tsx", all_paths) == "src/helpers.js"
    assert _resolve_js_import("./utils", "src/app.tsx", all_paths) == "src/utils/index.ts"
    assert _resolve_js_import("./missing", "src/app.tsx", all_paths) is None


# ── extract_local_dependencies ───────────────────────────────────────────────


def test_extract_local_dependencies_python_imports():
    all_paths = {
        "pkg/__init__.py",
        "pkg/core.py",
        "pkg/sub/helpers.py",
        "pkg/sub/mod.py",
        "src/other.py",
        "pkg/sibling.py",
    }
    content = (
        "import pkg.core\n"
        "import other\n"
        "from pkg.sub import helpers\n"
        "from . import sibling\n"
        "from .helpers import x\n"
        "from ..sibling import y\n"
    )

    deps = extract_local_dependencies("pkg/sub/mod.py", content, all_paths)

    assert deps == ["pkg/core.py", "pkg/sibling.py", "pkg/sub/helpers.py", "src/other.py"]


def test_extract_local_dependencies_python_syntax_error_and_no_paths():
    assert extract_local_dependencies("a.py", "def broken(:\n") == []
    assert extract_local_dependencies("a.py", "import os\n") == []


def test_extract_local_dependencies_javascript_imports():
    all_paths = {"src/app.ts", "src/lib/util.ts", "src/data.json"}
    content = (
        "import { a } from './lib/util';\nconst d = import('./data');\nimport x from 'react';\n"
    )

    deps = extract_local_dependencies("src/app.ts", content, all_paths)

    assert deps == ["src/data.json", "src/lib/util.ts"]


def test_extract_local_dependencies_ignores_other_suffixes():
    assert extract_local_dependencies("README.md", "import x", {"x.py"}) == []


# ── related_test_paths / related_source_paths ────────────────────────────────


def test_related_test_paths_conventions_and_fallback():
    assert related_test_paths("tests/test_app.py", {"tests/test_app.py"}) == []

    all_paths = {"src/pkg/app.py", "tests/pkg/test_app.py", "tests/pkg/app.py"}
    assert related_test_paths("src/pkg/app.py", all_paths) == [
        "tests/pkg/app.py",
        "tests/pkg/test_app.py",
    ]

    fallback = {"spec/app_test.py", "spec/other_test.py", "src/app.py"}
    assert related_test_paths("src/app.py", fallback) == ["spec/app_test.py"]

    assert related_test_paths("app.py", {"README.md"}) == []


def test_related_source_paths_strips_test_affixes():
    all_paths = {"src/app.py", "lib/app.js", "docs/app.md", "tests/test_app.py"}

    assert related_source_paths("src/app.py", all_paths) == []
    assert related_source_paths("tests/test_app.py", all_paths) == ["lib/app.js", "src/app.py"]
    assert related_source_paths("tests/app_test.py", all_paths) == ["lib/app.js", "src/app.py"]
    assert related_source_paths("tests/apptest.py", all_paths) == []


# ── collect_repo_files ───────────────────────────────────────────────────────


def test_collect_repo_files_respects_budget_and_limits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("pyproject.toml").write_text("[project]\n")
    Path("README.md").write_text("# readme\n")
    Path("src").mkdir()
    Path("src/app.py").write_text("x = 1\n")
    Path("src/big.py").write_text("y" * 400)
    Path("docs").mkdir()
    Path("docs/guide.md").write_text("# guide\n")
    Path("node_modules").mkdir()
    Path("node_modules/dep.js").write_text("skip\n")
    Path("huge.py").write_text("z" * (MAX_FILE_SIZE + 1))
    Path("image.png").write_bytes(b"\x89PNG")

    files = collect_repo_files()
    assert list(files) == [
        "pyproject.toml",
        "README.md",
        "src/app.py",
        "src/big.py",
        "docs/guide.md",
    ]

    budgeted = collect_repo_files(target_tokens=50)
    assert list(budgeted) == ["pyproject.toml", "README.md", "src/app.py", "docs/guide.md"]

    assert list(collect_repo_files(max_files=1)) == ["pyproject.toml"]


# ── list_repo_files ──────────────────────────────────────────────────────────


def test_list_repo_files_includes_metadata_and_relations(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("src").mkdir()
    Path("src/app.py").write_text("import src.util\n")
    Path("src/util.py").write_text("x = 1\n")
    Path("tests").mkdir()
    Path("tests/test_app.py").write_text("from src.app import x\n")
    Path("oversized.py").write_text("z" * (MAX_FILE_SIZE + 1))

    entries = {e["path"]: e for e in list_repo_files()}

    assert "oversized.py" not in entries
    app = entries["src/app.py"]
    assert app["kind"] == "source"
    assert app["size"] == len("import src.util\n")
    assert app["suffix"] == ".py"
    assert app["local_dependencies"] == ["src/util.py"]
    assert app["related_tests"] == ["tests/test_app.py"]
    assert app["related_sources"] == []
    assert entries["tests/test_app.py"]["related_sources"] == ["src/app.py"]


def test_list_repo_files_tolerates_files_vanishing_mid_walk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("keep.py").write_text("x = 1\n")
    Path("gone.py").write_text("y = 2\n")
    real_is_source = repo_context._is_source_file

    def vanish(p: Path) -> bool:
        ok = real_is_source(p)
        if ok and p.name == "gone.py":
            p.unlink()
        return ok

    monkeypatch.setattr(repo_context, "_is_source_file", vanish)

    assert [e["path"] for e in list_repo_files()] == ["keep.py"]


# ── collect_specific_files / expand_context_paths ────────────────────────────


def test_collect_specific_files_limits_and_budget(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("a.py").write_text("a" * 100)
    Path("b.py").write_text("b" * 100)
    Path("c.py").write_text("c\n")

    assert list(collect_specific_files(["a.py", "../x.py", "missing.py", "b.py"])) == [
        "a.py",
        "b.py",
    ]
    assert list(collect_specific_files(["a.py", "b.py"], max_files=1)) == ["a.py"]
    assert list(collect_specific_files(["a.py", "b.py", "c.py"], target_tokens=45)) == [
        "a.py",
        "c.py",
    ]


def test_expand_context_paths_pulls_in_related_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("src").mkdir()
    Path("src/app.py").write_text("import src.util\n")
    Path("src/util.py").write_text("x = 1\n")
    Path("tests").mkdir()
    Path("tests/test_app.py").write_text("from src.app import x\n")

    assert expand_context_paths([]) == {}

    files = expand_context_paths(["src/app.py", "unknown.py"])

    assert list(files) == ["src/app.py", "tests/test_app.py", "src/util.py"]


def test_expand_context_paths_ignores_non_string_relations(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("app.py").write_text("x = 1\n")
    monkeypatch.setattr(
        repo_context,
        "list_repo_files",
        lambda: [{"path": "app.py", "related_tests": [None, 3], "local_dependencies": "nope"}],
    )

    assert list(expand_context_paths(["app.py"])) == ["app.py"]


# ── estimate_tokens / build_context_string / compress_patch ──────────────────


def test_estimate_tokens_and_context_string():
    files = {"a.py": "x = 1"}

    assert estimate_tokens(files) == (len("a.py") + len("x = 1") + 30) // 4
    assert build_context_string(files) == "### a.py\n```\nx = 1\n```"


def test_compress_patch_keeps_short_patches_and_important_lines():
    short = "diff --git a/x b/x\n+added"
    assert compress_patch(short) == short

    lines = ["diff --git a/x b/x", "--- a/x", "+++ b/x", "@@ -1,3 +1,3 @@"]
    lines += [" unchanged_body"] * 40 + ["-old", "+new"]
    patch = "\n".join(lines)

    compressed = compress_patch(patch, max_chars=200)

    assert compressed.startswith("[RepoKeeper compressed diff: omitted 40 unchanged context lines]")
    assert "unchanged_body" not in compressed
    assert compressed.endswith("-old\n+new")


def test_compress_patch_truncates_middle_when_still_too_long():
    lines = ["diff --git a/x b/x"] + [f"+line {i}" for i in range(200)]
    patch = "\n".join(lines)

    compressed = compress_patch(patch, max_chars=300)

    assert "[RepoKeeper compressed diff: middle truncated]" in compressed
    assert compressed.startswith("[RepoKeeper compressed diff: omitted 0 unchanged")
    assert compressed.endswith("+line 199")
