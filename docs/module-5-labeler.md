# Module 5: Auto-Labeler 🏷️

The Auto-Labeler uses AI to automatically classify and label new GitHub issues
and pull requests. It fetches your repo's existing labels first, then picks from
them — keeping naming conventions and description styles consistent. Only creates
new labels when absolutely necessary.

## How It Works

1. **Fetches repo labels** — builds a catalog of all existing labels (name,
   description, color).
2. **Infers naming convention** — detects patterns like `area/module`,
   `type: bug`, `Priority: High`, kebab-case, etc.
3. **Classifies the issue/PR** — AI reads the title, body, and (for PRs) the
   changed files diff to determine the primary category.
4. **Resolves labels** — picks the best-fitting existing labels. Only creates
   new labels when none fit, matching the repo's naming convention and color
   palette.
5. **Applies or suggests** — in `add` mode, labels are applied directly. In
   `suggest` mode, a comment is posted for maintainer review.

## Triggering

### Workflow Dispatch (manual)

From the Actions tab, run the **RepoKeeper Auto-Labeler** workflow:

- Leave the `issue_number` empty for batch mode (all unlabeled open issues).
- Enter a specific issue number to label just that issue.

### Automatic (on new issues)

When the `labeler.yml` workflow is installed, it runs automatically on every
newly opened issue.

### CLI

```bash
# Label a specific issue
repokeeper labeler --repo owner/repo --issue 42

# Label a specific PR
repokeeper labeler --repo owner/repo --pr 42

# Label all unlabeled open issues (batch mode)
repokeeper labeler --repo owner/repo

# With markdown summary
repokeeper labeler --repo owner/repo --summary
```

## Installation

Copy the workflow template into your repo:

```bash
cp src/repokeeper/templates/workflows/labeler.yml .github/workflows/labeler.yml
```

Or run `repokeeper doctor --repo owner/repo` to diagnose missing workflows.

## Configuration

Add a `labeler` section to your maintainer profile (`repokeeper.yml`):

```yaml
labeler:
  enabled: true
  mode: add                 # "add" = apply labels directly, "suggest" = post comment
  confidence_threshold: 0.7 # minimum AI confidence to apply labels
  max_labels: 3             # max labels to apply per issue/PR
  allow_create_labels: true # allow creating new labels when needed
  label_map:                # optional: maps AI category to your GitHub labels
    bug: ["bug"]
    feature_request: ["enhancement"]
    question: ["question"]
    documentation: ["documentation"]
  exclude_labels:           # labels to ignore when finding unlabeled issues
    - "repokeeper-labeler"
```

| Setting | Default | Description |
|---|---|---|
| `enabled` | `true` | Enable/disable the labeler |
| `mode` | `add` | `add` (apply directly) or `suggest` (post comment) |
| `confidence_threshold` | `0.7` | Minimum AI confidence (0–1) to apply labels |
| `max_labels` | `3` | Max labels to apply per item |
| `allow_create_labels` | `true` | Allow creating new labels when no existing one fits |
| `label_map` | `{}` | Map AI categories to your specific GitHub label names |
| `exclude_labels` | `[]` | Labels to ignore when finding unlabeled issues |

## Issue Categories

The labeler classifies issues into these categories:

| Category | Description |
|---|---|
| `bug` | Something isn't working |
| `feature_request` | New feature or enhancement |
| `question` | Request for information or clarification |
| `documentation` | Docs improvements |
| `performance` | Performance issues or optimizations |
| `security` | Security vulnerabilities or concerns |
| `dependencies` | Dependency updates or issues |
| `duplicate` | Duplicate of an existing issue |
| `wontfix` | Won't be fixed |
| `good_first_issue` | Good for newcomers |
| `help_wanted` | Extra attention/maintainer help needed |
| `refactoring` | Code restructuring without feature changes |
| `testing` | Test improvements or failures |
| `ci_cd` | CI/CD pipeline issues |
| `noise` | Unactionable or spam |

## PR Classification (Diff-Aware)

For pull requests, the labeler considers **changed files** to determine the
**PRIMARY** purpose:

- A feature PR that also touches 2 docs files → `enhancement` (not `documentation`)
- A bugfix that also touches test files → `bug` (not `testing`)
- A PR touching `.github/workflows/` → may get `ci_cd`
- A very small PR (≤50 lines) → may get `good_first_issue`

## Batch Mode

When no `--issue` or `--pr` is specified, the labeler scans all open issues
and labels those that have no meaningful labels yet (excluding labels in
`exclude_labels`). This is useful for:

- Initial labeling of a backlog
- Periodic cleanup of unlabeled issues
- Running from a cron schedule

## Report Summary

The labeler produces a markdown summary with sections:

- ✅ **Labels Applied** — issues/PRs that were labeled with AI confidence
- 💬 **Suggestions Posted** — comments left for manual review (suggest mode)
- ⏭️ **Skipped** — items skipped (low confidence, no matches, etc.)
- ❌ **Errors** — items that failed to process

## RepoKeeper Label

All labeled issues receive the `repokeeper-labeler` label for tracking. This
label is automatically excluded from batch mode scans so items are not
re-processed.

## See Also

- [Module 4: Maintainer Profile](module-4-profile.md) — profile configuration
- [Setup Guide](setup.md) — setting up RepoKeeper
