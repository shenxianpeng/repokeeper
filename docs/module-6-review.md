# Module 6: Code Review Agent 🔍

The Code Review Agent reads a pull request diff along with relevant codebase
context, then posts a structured code review comment. It uses your Maintainer
Profile to check code style, tech stack preferences, PR standards, and overall
code quality.

> ⚠️ **Safety**: RepoKeeper **never** approves or merges PRs. It only provides
> review suggestions for the human maintainer.

## How It Works

1. **Triggered by label or comment** — add the `agent-review` label to a PR, or
   comment `@repokeeper review`.
2. **Reads PR diff** — fetches all changed files with patches (up to a size
   limit).
3. **Collects repo context** — scans relevant files for tech stack, code style,
   and patterns.
4. **Loads Maintainer Profile** — reads your code style, tone, PR standards
   from `repokeeper.yml`.
5. **Posts structured review** — formats findings as a GitHub comment with
   severity levels, code snippets, and suggestions.

## Triggering

### Method 1: Label

Add the `agent-review` label to any pull request:

```
PR → Labels → agent-review
```

### Method 2: Comment

Comment `@repokeeper review` on a pull request (must be a repo collaborator,
member, or owner):

```
@repokeeper review
```

### CLI

```bash
repokeeper review --repo owner/repo --pr 42
```

## Installation

Copy the workflow template into your repo:

```bash
cp src/repokeeper/templates/workflows/review.yml .github/workflows/review.yml
```

Or run `repokeeper doctor --repo owner/repo` to diagnose and fix missing
workflows.

## What It Checks

The review agent evaluates:

| Area | What it checks |
|---|---|
| **Code Style** | Consistency with maintainer profile (indentation, naming, patterns) |
| **Tech Stack** | Does the implementation use your preferred stack/patterns? |
| **PR Standards** | File count, diff size, test coverage |
| **Code Quality** | Potential bugs, edge cases, performance issues |
| **Documentation** | Are new features documented? |
| **Test Coverage** | Are there tests for the changes? |

## Review Comment Format

The review is posted as a structured GitHub comment with:

- **🔴 Critical** — must-fix issues
- **🟡 Warning** — should-fix, but not blocking
- **🔵 Suggestion** — nice-to-have improvements
- **💡 Tip** — helpful observations

Each finding includes the relevant code snippet and a specific suggestion.

## Configuration

The review agent reads from your Maintainer Profile (`repokeeper.yml`):

```yaml
# Code standards the review agent checks against
code_style:
  max_line_length: 100
  indent: 4
  naming: snake_case
  prefer_type_hints: true

# Tech stack preferences
tech_stack:
  language: python
  framework: fastapi
  testing: pytest

# PR standards
pr_standards:
  max_files_changed: 10
  max_diff_lines: 500
  require_tests: true
```

## See Also

- [Module 4: Maintainer Profile](module-4-profile.md) — profile configuration
- [Setup Guide](setup.md) — setting up RepoKeeper
