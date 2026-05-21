"""Tests for repokeeper.search."""

from __future__ import annotations

from pathlib import Path

from repokeeper.search import (
    _is_keyword,
    _parse_git_grep_output,
    discover_related_files,
    search_codebase,
    summarize_search_results,
)


class TestIsKeyword:
    def test_plain_word(self) -> None:
        assert _is_keyword("hello") is True

    def test_with_underscore(self) -> None:
        assert _is_keyword("my_function") is True

    def test_with_regex_meta(self) -> None:
        assert _is_keyword(r"\d+") is False

    def test_with_dot(self) -> None:
        assert _is_keyword("hello.world") is False


class TestSearchCodebase:
    def test_search_finds_match(self, tmp_path: Path) -> None:
        (tmp_path / "test.py").write_text("def hello_world():\n    return 42\n")
        results = search_codebase("hello_world", repo_root=tmp_path)
        assert len(results) >= 1
        assert any("hello_world" in r["line"] for r in results)
        # verify structure
        for r in results:
            assert "file" in r
            assert "line_number" in r
            assert "line" in r

    def test_search_no_match(self, tmp_path: Path) -> None:
        (tmp_path / "test.py").write_text("def foo():\n    pass\n")
        results = search_codebase("nonexistent_pattern_xyz", repo_root=tmp_path)
        assert len(results) == 0

    def test_search_multiple_matches(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("import foo\nfoo.bar()\n")
        (tmp_path / "b.py").write_text("from foo import bar\nbar()\n")
        results = search_codebase("foo", repo_root=tmp_path)
        assert len(results) >= 2

    def test_search_handles_binary(self, tmp_path: Path) -> None:
        (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02")
        results = search_codebase("anything", repo_root=tmp_path)
        assert len(results) == 0

    def test_respects_max_results(self, tmp_path: Path) -> None:
        for i in range(5):
            (tmp_path / f"file_{i}.py").write_text(f"# tag_{i}\nprint('hello')\n")
        results = search_codebase("hello", repo_root=tmp_path, max_results=2)
        assert len(results) <= 2

    def test_search_with_glob_pattern(self, tmp_path: Path) -> None:
        (tmp_path / "test.py").write_text("hello world\n")
        (tmp_path / "README.md").write_text("hello docs\n")
        results = search_codebase(
            "hello", file_patterns=["*.py"], repo_root=tmp_path,
        )
        assert all(r["file"].endswith(".py") for r in results)

    def test_line_numbers_are_correct(self, tmp_path: Path) -> None:
        (tmp_path / "test.py").write_text("line1\nline2 target\nline3\n")
        results = search_codebase("target", repo_root=tmp_path)
        assert len(results) >= 1
        assert results[0]["line_number"] == 2

    def test_skip_large_files(self, tmp_path: Path) -> None:
        (tmp_path / "big.py").write_text("x" * 500_000)
        results = search_codebase("x", repo_root=tmp_path, max_file_size_kb=10)
        # Should only find matches in small files, not big.py
        for r in results:
            assert r["file"] != "big.py"


class TestSummarizeSearchResults:
    def test_no_matches(self) -> None:
        result = summarize_search_results([], "pattern")
        assert "no matches" in result

    def test_with_matches(self) -> None:
        matches = [
            {"file": "test.py", "line_number": 1, "line": "def foo():"},
            {"file": "test.py", "line_number": 5, "line": "    bar()"},
        ]
        result = summarize_search_results(matches, "pattern")
        assert "test.py" in result
        assert "def foo()" in result

    def test_multiple_files(self) -> None:
        matches = [
            {"file": "a.py", "line_number": 1, "line": "x"},
            {"file": "b.py", "line_number": 1, "line": "y"},
        ]
        result = summarize_search_results(matches, "p")
        assert "a.py" in result
        assert "b.py" in result


class TestDiscoverRelatedFiles:
    def test_empty_seeds(self, tmp_path: Path) -> None:
        result = discover_related_files([], repo_root=tmp_path)
        assert result == []

    def test_with_seeds_no_related(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "foo.py").write_text("def bar():\n    pass\n")
        result = discover_related_files(["src/foo.py"], repo_root=tmp_path)
        # May find related files if there are conventions — at minimum
        # it should not crash.
        assert isinstance(result, list)

    def test_finds_test_files(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "foo.py").write_text("def bar():\n    pass\n")
        (tmp_path / "tests" / "test_foo.py").write_text(
            "from src.foo import bar\n\ndef test_bar():\n    pass\n"
        )
        result = discover_related_files(["src/foo.py"], repo_root=tmp_path)
        assert "tests/test_foo.py" in result


class TestParseGitGrepOutput:
    def test_empty_output(self, tmp_path: Path) -> None:
        result = _parse_git_grep_output("", 50, tmp_path)
        assert result == []

    def test_heading_format(self, tmp_path: Path) -> None:
        output = "src/foo.py\n1:def bar():\n"
        result = _parse_git_grep_output(output, 50, tmp_path)
        assert len(result) >= 1
        assert result[0]["file"] == "src/foo.py"
        assert result[0]["line_number"] == 1

    def test_full_path_format(self, tmp_path: Path) -> None:
        output = "src/foo.py:10:    return bar()\n"
        result = _parse_git_grep_output(output, 50, tmp_path)
        assert len(result) >= 1
        assert result[0]["file"] == "src/foo.py"
        assert result[0]["line_number"] == 10
        assert "return bar()" in result[0]["line"]

    def test_respects_max_results(self, tmp_path: Path) -> None:
        output = """src/a.py:1:a
src/b.py:1:b
src/c.py:1:c
"""
        result = _parse_git_grep_output(output, 2, tmp_path)
        assert len(result) <= 2

    def test_multiple_matches_in_same_file(self, tmp_path: Path) -> None:
        output = "src/foo.py\n1:def foo():\n5:    foo()\n"
        result = _parse_git_grep_output(output, 50, tmp_path)
        assert len(result) == 2
        assert all(r["file"] == "src/foo.py" for r in result)

    def test_empty_lines_skipped(self, tmp_path: Path) -> None:
        output = "\n\nsrc/foo.py\n1:code\n\n"
        result = _parse_git_grep_output(output, 50, tmp_path)
        assert len(result) >= 1

    def test_line_without_colon(self, tmp_path: Path) -> None:
        output = "just_a_line_without_colon\nsrc/foo.py\n1:code\n"
        result = _parse_git_grep_output(output, 50, tmp_path)
        assert len(result) >= 1
