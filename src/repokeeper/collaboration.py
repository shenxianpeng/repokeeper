"""Shared GitHub-native handoff primitives for RepoKeeper modules."""

from __future__ import annotations

CANDIDATE_LABEL = "repokeeper-candidate"
RADAR_LABEL = "repokeeper-radar"
PATROL_LABEL = "repokeeper-patrol"
REVIEW_LABEL = "agent-review"
AGENT_TODO_LABEL = "agent-todo"

_LABEL_STYLES = {
    CANDIDATE_LABEL: ("fef3c7", "RepoKeeper candidate waiting for maintainer approval"),
    RADAR_LABEL: ("dbeafe", "Created or updated by RepoKeeper Radar"),
    PATROL_LABEL: ("dcfce7", "Created or updated by RepoKeeper Patrol"),
}


def candidate_labels(source_label: str, labels: list[str] | None = None) -> list[str]:
    """Return deduplicated candidate labels without implementation triggers."""
    raw_labels = [CANDIDATE_LABEL, source_label] + list(labels or [])
    return [
        label
        for label in dict.fromkeys(raw_labels)
        if label and label != AGENT_TODO_LABEL
    ]


def ensure_github_labels(gh_repo: object, labels: list[str]) -> None:
    """Best-effort creation for RepoKeeper-owned labels."""
    for label in labels:
        if label not in _LABEL_STYLES:
            continue

        try:
            gh_repo.get_label(label)  # type: ignore[attr-defined]
            continue
        except Exception:
            pass

        color, description = _LABEL_STYLES[label]
        try:
            gh_repo.create_label(  # type: ignore[attr-defined]
                name=label,
                color=color,
                description=description,
            )
        except Exception:
            continue


def format_candidate_block(
    *,
    source_module: str,
    recommended_action: str,
    summary: str,
    source_url: str = "",
    confidence: float | None = None,
    risk: str = "",
    acceptance: str = "",
) -> str:
    """Build a structured maintainer approval block."""
    lines = [
        "## RepoKeeper Candidate",
        "",
        f"- **Source module:** {source_module}",
        f"- **Recommended action:** {recommended_action}",
    ]
    if confidence is not None:
        lines.append(f"- **Confidence:** {confidence:.2f}")
    if risk:
        lines.append(f"- **Risk:** {risk}")
    if source_url:
        lines.append(f"- **Original source:** {source_url}")
    lines.extend([
        f"- **Diagnosis summary:** {summary or 'Needs maintainer review.'}",
        f"- **Suggested acceptance criteria:** {acceptance or 'Maintainer reviews and approves the next action.'}",
        "",
        (
            "Maintainer approval required: add `agent-todo` or comment "
            "`@repokeeper go` to request implementation."
        ),
    ])
    return "\n".join(lines)
