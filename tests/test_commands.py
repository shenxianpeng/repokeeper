"""Tests for repokeeper.commands."""

from __future__ import annotations

from repokeeper.commands import (
    build_status_comment,
    has_trigger,
    help_text,
    parse_commands,
)


class TestParseCommands:
    def test_empty(self) -> None:
        result = parse_commands("")
        assert not result.has_any
        assert result.commands == []

    def test_no_commands(self) -> None:
        result = parse_commands("Just a regular comment\nwith multiple lines")
        assert not result.has_any

    def test_single_go(self) -> None:
        result = parse_commands("/repokeeper go")
        assert result.has_any
        assert result.first_verb == "go"
        assert len(result.commands) == 1
        assert result.commands[0].verb == "go"
        assert result.commands[0].args == []

    def test_go_case_insensitive(self) -> None:
        result = parse_commands("/REPOKEEPER GO")
        assert result.has_any
        assert result.first_verb == "go"

    def test_review_command(self) -> None:
        result = parse_commands("/repokeeper review")
        assert result.has_any
        assert result.first_verb == "review"

    def test_describe_command(self) -> None:
        result = parse_commands("/repokeeper describe")
        assert result.first_verb == "describe"

    def test_label_command(self) -> None:
        result = parse_commands("/repokeeper label")
        assert result.first_verb == "label"

    def test_fix_command(self) -> None:
        result = parse_commands("/repokeeper fix")
        assert result.first_verb == "fix"

    def test_status_command(self) -> None:
        result = parse_commands("/repokeeper status")
        assert result.first_verb == "status"

    def test_help_command(self) -> None:
        result = parse_commands("/repokeeper help")
        assert result.first_verb == "help"

    def test_multiple_commands(self) -> None:
        result = parse_commands("/repokeeper go\n/repokeeper review")
        assert len(result.commands) == 2
        assert result.commands[0].verb == "go"
        assert result.commands[1].verb == "review"

    def test_command_with_args(self) -> None:
        result = parse_commands("/repokeeper go --dry-run")
        assert len(result.commands) == 1
        assert result.commands[0].verb == "go"
        assert result.commands[0].args == ["--dry-run"]

    def test_leading_whitespace(self) -> None:
        result = parse_commands("  /repokeeper help  ")
        assert result.has_any
        assert result.first_verb == "help"

    def test_unknown_command_ignored(self) -> None:
        result = parse_commands("/repokeeper foobar")
        assert not result.has_any

    def test_command_in_middle_of_comment(self) -> None:
        result = parse_commands(
            "Thanks for the issue.\n\n/repokeeper go\n\nI'll review the PR afterward."
        )
        assert result.has_any
        assert result.commands[0].verb == "go"
        # line_index should reflect the actual line
        assert result.commands[0].line_index == 2

    def test_has_restricted_flag(self) -> None:
        result = parse_commands("/repokeeper go")
        assert result.has_restricted


class TestHasTrigger:
    def test_has_go(self) -> None:
        assert has_trigger("/repokeeper go")

    def test_has_review(self) -> None:
        assert has_trigger("/repokeeper review")

    def test_no_trigger(self) -> None:
        assert not has_trigger("just a comment")

    def test_empty(self) -> None:
        assert not has_trigger("")

    def test_partial_match_not_triggered(self) -> None:
        # "/repokeeper" alone (no verb) should not trigger
        assert not has_trigger("/repokeeper")

    def test_case_insensitive(self) -> None:
        assert has_trigger("/REPOKEEPER help")


class TestHelpText:
    def test_help_text_non_empty(self) -> None:
        text = help_text()
        assert len(text) > 0
        assert "RepoKeeper" in text

    def test_lists_all_commands(self) -> None:
        text = help_text()
        for cmd in ("go", "review", "describe", "label", "fix", "status", "help"):
            assert f"`/repokeeper {cmd}`" in text


class TestBuildStatusComment:
    def test_empty_activity(self) -> None:
        issue_data = {"number": 42, "title": "test issue", "repo": "owner/repo"}
        result = build_status_comment(issue_data, {})
        assert "RepoKeeper Status" in result
        assert "No RepoKeeper activity yet" in result

    def test_with_activity(self) -> None:
        issue_data = {"number": 42, "title": "test issue", "repo": "owner/repo"}
        activity = {
            "agent_comments": [
                {"author": "repokeeper[bot]", "body": "Working on it..."},
            ],
            "prs_created": ["https://github.com/owner/repo/pull/99"],
            "labels_applied": ["agent-todo"],
        }
        result = build_status_comment(issue_data, activity)
        assert "Working on it" in result
        assert "https://github.com/owner/repo/pull/99" in result
        assert "agent-todo" in result

    def test_with_all_fields(self) -> None:
        issue_data = {"number": 1, "title": "test", "repo": "a/b"}
        activity = {
            "agent_comments": [{"author": "bot", "body": "ok"}],
            "prs_created": ["url1"],
            "reviews_posted": ["review1"],
            "labels_applied": ["l1"],
            "fixes_applied": ["fix1"],
        }
        result = build_status_comment(issue_data, activity)
        assert "ok" in result
        assert "url1" in result
        assert "review1" in result
        assert "l1" in result
        assert "fix1" in result


class MockIssue:
    def create_comment(self, body: str) -> None:
        pass


def test_handle_help_command_failure(monkeypatch) -> None:
    """handle_help_command returns False when create_comment fails."""
    from repokeeper.commands import handle_help_command

    class FailingIssue:
        def create_comment(self, body: str) -> None:
            raise RuntimeError("cannot comment")

    result = handle_help_command(FailingIssue())
    assert result is False
