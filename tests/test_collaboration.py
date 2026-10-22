"""Tests for the shared GitHub handoff primitives (collaboration.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

from repokeeper.collaboration import (
    AGENT_TODO_LABEL,
    CANDIDATE_LABEL,
    PATROL_LABEL,
    RADAR_LABEL,
    candidate_labels,
    ensure_github_labels,
    format_candidate_block,
)

# ── candidate_labels ─────────────────────────────────────────────────────────


def test_candidate_labels_dedupes_and_drops_trigger_label():
    labels = candidate_labels(RADAR_LABEL, ["bug", RADAR_LABEL, AGENT_TODO_LABEL, "", "bug"])

    assert labels == [CANDIDATE_LABEL, RADAR_LABEL, "bug"]


def test_candidate_labels_without_extra_labels():
    assert candidate_labels(PATROL_LABEL) == [CANDIDATE_LABEL, PATROL_LABEL]


# ── ensure_github_labels ─────────────────────────────────────────────────────


def test_ensure_github_labels_skips_unknown_and_existing_labels():
    gh_repo = MagicMock()

    ensure_github_labels(gh_repo, ["bug", RADAR_LABEL])

    gh_repo.get_label.assert_called_once_with(RADAR_LABEL)
    gh_repo.create_label.assert_not_called()


def test_ensure_github_labels_creates_missing_label():
    gh_repo = MagicMock()
    gh_repo.get_label.side_effect = Exception("404")

    ensure_github_labels(gh_repo, [PATROL_LABEL])

    gh_repo.create_label.assert_called_once_with(
        name=PATROL_LABEL,
        color="dcfce7",
        description="Created or updated by RepoKeeper Patrol",
    )


def test_ensure_github_labels_ignores_create_failures():
    gh_repo = MagicMock()
    gh_repo.get_label.side_effect = Exception("404")
    gh_repo.create_label.side_effect = Exception("forbidden")

    ensure_github_labels(gh_repo, [CANDIDATE_LABEL, RADAR_LABEL])

    assert gh_repo.create_label.call_count == 2


# ── format_candidate_block ───────────────────────────────────────────────────


def test_format_candidate_block_includes_optional_fields():
    block = format_candidate_block(
        source_module="radar",
        recommended_action="Open a PR",
        summary="Dependency is outdated",
        source_url="https://example.test/issue/1",
        confidence=0.876,
        risk="low",
        acceptance="CI stays green",
    )

    assert "- **Source module:** radar" in block
    assert "- **Recommended action:** Open a PR" in block
    assert "- **Confidence:** 0.88" in block
    assert "- **Risk:** low" in block
    assert "- **Original source:** https://example.test/issue/1" in block
    assert "- **Diagnosis summary:** Dependency is outdated" in block
    assert "- **Suggested acceptance criteria:** CI stays green" in block
    assert "/repokeeper go" in block


def test_format_candidate_block_uses_defaults_when_fields_empty():
    block = format_candidate_block(
        source_module="patrol",
        recommended_action="Close",
        summary="",
    )

    assert "Confidence" not in block
    assert "Risk" not in block
    assert "Original source" not in block
    assert "- **Diagnosis summary:** Needs maintainer review." in block
    assert (
        "- **Suggested acceptance criteria:** Maintainer reviews and approves the next action."
        in block
    )
